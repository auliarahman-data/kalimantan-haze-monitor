#!/usr/bin/env python3
"""Fetch recent VIIRS (S-NPP) active-fire detections for Borneo from NASA FIRMS
and merge them into data/fire_hotspots.csv.

- The CSV in this repo is the permanent source of truth (BigQuery sandbox tables
  expire after 60 days, so BigQuery is rebuilt from this file).
- Each run re-fetches the last N days (default 3). Rows already in the CSV are
  skipped, so re-running never creates duplicates.
- A fire detection is identified by (detected_at_utc, latitude, longitude).

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCE = "VIIRS_SNPP_NRT"
BBOX = "108.5,-4.5,119.5,4.5"          # west,south,east,north (all of Borneo)
OUT = Path("data/fire_hotspots.csv")
MAX_DAYS_PER_CALL = 5                   # FIRMS limit per request

EXPECTED_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight"
)
FIELDS = [
    "detected_at_utc", "acq_date", "latitude", "longitude", "bright_ti4",
    "bright_ti5", "scan", "track", "satellite", "instrument", "confidence",
    "frp", "daynight", "version",
]


def row_key(row: dict) -> tuple:
    return (row["detected_at_utc"], row["latitude"], row["longitude"])


def parse_rows(text: str) -> list[dict]:
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
        })
    return rows


def fetch_chunk(map_key: str, start: date, days: int) -> list[dict]:
    """One API call: `days` days starting at `start` (forward in time)."""
    url = f"{API}/{map_key}/{SOURCE}/{BBOX}/{days}/{start.isoformat()}"
    last_error = "unknown"
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=60)
        except requests.RequestException as exc:
            # exception text can include the URL (and the key): print type only
            last_error = type(exc).__name__
        else:
            if resp.status_code == 200:
                return parse_rows(resp.text)
            last_error = f"HTTP {resp.status_code}"
        print(f"  attempt {attempt} failed ({last_error}), retrying in 10s")
        time.sleep(10)
    raise RuntimeError(f"FIRMS request failed after 3 attempts: {last_error}")


def read_existing(path: Path) -> dict[tuple, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row_key(r): r for r in csv.DictReader(f)}


def write_merged(path: Path, rows: dict[tuple, dict]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3,
                        help="how many days back to fetch, ending today (UTC)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not map_key:
        print("FIRMS_MAP_KEY is not set", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=args.days - 1)

    existing = read_existing(args.out)
    before = len(existing)
    fetched = 0
    cursor, remaining = start, args.days
    while remaining > 0:
        step = min(MAX_DAYS_PER_CALL, remaining)
        rows = fetch_chunk(map_key, cursor, step)
        fetched += len(rows)
        for r in rows:
            existing.setdefault(row_key(r), r)
        cursor += timedelta(days=step)
        remaining -= step

    write_merged(args.out, existing)
    print(f"window: {start} .. {today} (UTC) | fetched: {fetched} | "
          f"new: {len(existing) - before} | total rows: {len(existing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
