#!/usr/bin/env python3
"""Fetch VIIRS (S-NPP) active-fire detections for Borneo from NASA FIRMS and
merge them into monthly CSVs: data/fire_hotspots/YYYY-MM.csv.

- The CSVs in this repo are the permanent source of truth (BigQuery sandbox
  tables expire after 60 days, so BigQuery is rebuilt from these files).
- Files are split per month (by acq_date, UTC) so no single file grows past
  GitHub's file size limits.
- A fire detection is identified by (detected_at_utc, latitude, longitude).
  Re-running never creates duplicates.

Sources:
- NRT  = VIIRS_SNPP_NRT, near real-time, used by the daily run.
- SP   = VIIRS_SNPP_SP, standard (science-quality) processing, published with
         a delay of a few months. Used for older days in a backfill.
- auto = per day: SP where FIRMS says SP is available, NRT otherwise.
Each day comes from one source only. When a day is fetched from one source,
rows for that day from the other source are dropped, so NRT and SP versions
of the same fire are never counted twice.

Usage:
  python fetch_firms.py                          # last 3 days, NRT (daily run)
  python fetch_firms.py --days 10
  python fetch_firms.py --start 2026-06-30 --end 2026-10-15 --source auto

The MAP_KEY comes from the FIRMS_MAP_KEY environment variable (GitHub secret).
Error messages never print the request URL, because the URL contains the key.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HOST = "https://firms.modaps.eosdis.nasa.gov"
API = f"{HOST}/api/area/csv"
AVAILABILITY_API = f"{HOST}/api/data_availability/csv"
SOURCES = {"NRT": "VIIRS_SNPP_NRT", "SP": "VIIRS_SNPP_SP"}
BBOX = "108.5,-4.5,119.5,4.5"          # west,south,east,north (all of Borneo)
OUT_DIR = Path("data/fire_hotspots")
MAX_DAYS_PER_CALL = 5                   # FIRMS limit per request

EXPECTED_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight"
)
FIELDS = [
    "detected_at_utc", "acq_date", "latitude", "longitude", "bright_ti4",
    "bright_ti5", "scan", "track", "satellite", "instrument", "confidence",
    "frp", "daynight", "version", "source",
]


def row_key(row: dict) -> tuple:
    return (row["detected_at_utc"], row["latitude"], row["longitude"])


def parse_rows(text: str, source: str) -> list[dict]:
    """Turn a FIRMS CSV response into rows with a combined UTC timestamp."""
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != EXPECTED_HEADER:
        # FIRMS reports problems (bad key, limits) as plain text, not CSV
        snippet = (lines[0] if lines else "empty response")[:120]
        raise RuntimeError(f"Unexpected FIRMS response: {snippet}")
    rows = []
    for r in csv.DictReader(io.StringIO(text.strip())):
        hhmm = r["acq_time"].zfill(4)   # FIRMS drops leading zeros: 540 -> 0540
        detected = datetime.strptime(r["acq_date"] + hhmm, "%Y-%m-%d%H%M")
        rows.append({
            "detected_at_utc": detected.strftime("%Y-%m-%d %H:%M:%S"),
            "acq_date": r["acq_date"],
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "bright_ti4": r["bright_ti4"],
            "bright_ti5": r["bright_ti5"],
            "scan": r["scan"],
            "track": r["track"],
            "satellite": r["satellite"],
            "instrument": r["instrument"],
            "confidence": r["confidence"],
            "frp": r["frp"],
            "daynight": r["daynight"],
            "version": r["version"],
            "source": source,
        })
    return rows


def get(url: str) -> str:
    """GET with 3 attempts. Never prints the URL (it contains the key)."""
    last_error = "unknown"
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=90)
        except requests.RequestException as exc:
            # exception text can include the URL (and the key): print type only
            last_error = type(exc).__name__
        else:
            if resp.status_code == 200:
                return resp.text
            last_error = f"HTTP {resp.status_code}"
        print(f"  attempt {attempt} failed ({last_error}), retrying in 10s")
        time.sleep(10)
    raise RuntimeError(f"FIRMS request failed after 3 attempts: {last_error}")


def fetch_chunk(map_key: str, source: str, start: date, days: int) -> list[dict]:
    """One API call: `days` days starting at `start` (forward in time)."""
    url = f"{API}/{map_key}/{source}/{BBOX}/{days}/{start.isoformat()}"
    return parse_rows(get(url), source)


def availability(map_key: str, source: str) -> tuple[date, date]:
    """(min_date, max_date) for a source, from FIRMS /api/data_availability."""
    text = get(f"{AVAILABILITY_API}/{map_key}/{source}")
    for r in csv.DictReader(io.StringIO(text.strip())):
        if r.get("data_id") == source:
            return (date.fromisoformat(r["min_date"]),
                    date.fromisoformat(r["max_date"]))
    raise RuntimeError(f"No availability row for {source}: {text.strip()[:120]}")


def month_file(month: str) -> Path:
    return OUT_DIR / f"{month}.csv"


def read_month(month: str) -> dict[tuple, dict]:
    path = month_file(month)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = {}
        for r in csv.DictReader(f):
            r.setdefault("source", SOURCES["NRT"])  # files from before the source column
            r["source"] = r["source"] or SOURCES["NRT"]
            rows[row_key(r)] = r
        return rows


def write_month(month: str, rows: dict[tuple, dict]) -> None:
    path = month_file(month)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    ordered = sorted(
        rows.values(),
        key=lambda r: (r["detected_at_utc"], float(r["latitude"]), float(r["longitude"])),
    )
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    tmp.replace(path)


def plan_days(start: date, end: date, mode: str, map_key: str) -> list[tuple[str, date, int]]:
    """Split [start, end] into (source, chunk_start, days) calls of <= 5 days."""
    per_day = []
    if mode == "auto":
        sp_min, sp_max = availability(map_key, SOURCES["SP"])
        print(f"SP available: {sp_min} .. {sp_max}")
    d = start
    while d <= end:
        if mode == "auto":
            src = SOURCES["SP"] if sp_min <= d <= sp_max else SOURCES["NRT"]
        else:
            src = SOURCES[mode]
        per_day.append((src, d))
        d += timedelta(days=1)

    calls = []
    for src, d in per_day:
        if calls and calls[-1][0] == src and calls[-1][2] < MAX_DAYS_PER_CALL \
                and calls[-1][1] + timedelta(days=calls[-1][2]) == d:
            calls[-1] = (src, calls[-1][1], calls[-1][2] + 1)
        else:
            calls.append((src, d, 1))
    return calls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3,
                        help="how many days back to fetch, ending today (UTC)")
    parser.add_argument("--start", type=date.fromisoformat,
                        help="first UTC date (YYYY-MM-DD); overrides --days")
    parser.add_argument("--end", type=date.fromisoformat,
                        help="last UTC date (default: today UTC)")
    parser.add_argument("--source", choices=["NRT", "SP", "auto"], default="NRT")
    args = parser.parse_args()

    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not map_key:
        print("FIRMS_MAP_KEY is not set", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    end = min(args.end or today, today)
    start = args.start or (today - timedelta(days=args.days - 1))
    if start > end:
        print(f"start {start} is after end {end}", file=sys.stderr)
        return 1

    calls = plan_days(start, end, args.source, map_key)

    # fetch everything first, then merge month by month
    fetched: list[dict] = []
    for src, chunk_start, days in calls:
        rows = fetch_chunk(map_key, src, chunk_start, days)
        print(f"  {src} {chunk_start} +{days}d: {len(rows)} rows")
        fetched.extend(rows)

    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in fetched:
        by_month[r["acq_date"][:7]].append(r)

    total_new = total_replaced = 0
    for month, new_rows in sorted(by_month.items()):
        existing = read_month(month)
        before = len(existing)
        # a day fetched from one source replaces that day's rows from the other
        new_sources = {(r["acq_date"], r["source"]) for r in new_rows}
        new_days = {d for d, _ in new_sources}
        replaced = [k for k, r in existing.items()
                    if r["acq_date"] in new_days and (r["acq_date"], r["source"]) not in new_sources]
        for k in replaced:
            del existing[k]
        for r in new_rows:
            existing.setdefault(row_key(r), r)
        write_month(month, existing)
        total_new += len(existing) - before + len(replaced)
        total_replaced += len(replaced)
        print(f"{month}: {len(existing)} rows "
              f"(+{len(existing) - before + len(replaced)} new, {len(replaced)} replaced from other source)")

    print(f"window: {start} .. {end} (UTC) | calls: {len(calls)} | fetched: {len(fetched)} | "
          f"new: {total_new} | replaced: {total_replaced}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
