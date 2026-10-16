import argparse
import csv
import os
from datetime import date, datetime, timedelta, timezone

import requests

CITIES = [
    ("Banjarmasin", -3.32, 114.59),
    ("Palangka Raya", -2.21, 113.92),
    ("Pontianak", -0.03, 109.33),
    ("Samarinda", -0.50, 117.15),
    ("Balikpapan", -1.24, 116.83),
]
URL = "https://api.open-meteo.com/v1/forecast"
# Backfill uses the archive of the same forecast models; the live API
# only reaches 92 days back. Grid points may differ slightly (grid_lat/lon).
HISTORY_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OUT = "data/weather_hourly.csv"
VARS = ["temperature_2m", "relative_humidity_2m", "precipitation",
        "cloud_cover", "wind_speed_10m", "wind_direction_10m"]
FIELDS = ["city", "req_lat", "req_lon", "grid_lat", "grid_lon",
          "time_utc"] + VARS + ["fetched_at"]


def month_windows(start, end):
    """Split [start, end] into calendar-month (start, end) pairs."""
    s = start
    while s <= end:
        nxt = date(s.year + (s.month == 12), s.month % 12 + 1, 1)
        e = min(end, nxt - timedelta(days=1))
        yield s, e
        s = nxt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat,
                        help="backfill from this UTC date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat,
                        help="backfill up to this UTC date (default: today UTC)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    last_full_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    cutoff = last_full_hour.strftime("%Y-%m-%dT%H:%M")  # buang jam prakiraan
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    base = {
        "latitude": ",".join(str(c[1]) for c in CITIES),
        "longitude": ",".join(str(c[2]) for c in CITIES),
        "hourly": ",".join(VARS),
    }
    if args.start:
        # backfill: per bulan, hanya mengisi jam yang belum ada (data harian tidak ditimpa)
        end = min(args.end or now.date(), now.date())
        requests_to_make = [
            (HISTORY_URL, {**base, "start_date": s.isoformat(), "end_date": e.isoformat()})
            for s, e in month_windows(args.start, end)
        ]
        overwrite = False
    else:
        requests_to_make = [(URL, {**base, "past_days": 3, "forecast_days": 1})]
        overwrite = True

    # data lama dibaca dulu
    rows = {}
    if os.path.exists(OUT):
        with open(OUT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["city"], r["time_utc"])] = r

    new_count = 0
    for url, params in requests_to_make:
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
        results = resp.json()
        assert len(results) == len(CITIES), "jumlah blok tidak sama dengan jumlah kota"
        for (name, lat, lon), res in zip(CITIES, results):
            h = res["hourly"]
            for i, t in enumerate(h["time"]):
                if t > cutoff:  # buang prakiraan
                    continue
                if not overwrite and (name, t) in rows:
                    continue
                row = {
                    "city": name, "req_lat": lat, "req_lon": lon,
                    "grid_lat": res["latitude"], "grid_lon": res["longitude"],
                    "time_utc": t, "fetched_at": fetched_at,
                }
                for v in VARS:
                    row[v] = h[v][i]
                rows[(name, t)] = row
                new_count += 1
        if "start_date" in params:
            print(f"  {params['start_date']} .. {params['end_date']}: ok")

    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    print(f"baris diproses: {new_count}, total di file: {len(rows)}, cutoff: {cutoff}")


if __name__ == "__main__":
    main()
