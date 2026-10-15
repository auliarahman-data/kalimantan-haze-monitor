import csv
import os
from datetime import datetime, timedelta, timezone

import requests

CITIES = [
    ("Banjarmasin", -3.32, 114.59),
    ("Palangka Raya", -2.21, 113.92),
    ("Pontianak", -0.03, 109.33),
    ("Samarinda", -0.50, 117.15),
    ("Balikpapan", -1.24, 116.83),
]
URL = "https://api.open-meteo.com/v1/forecast"
OUT = "data/weather_hourly.csv"
VARS = ["temperature_2m", "relative_humidity_2m", "precipitation",
        "cloud_cover", "wind_speed_10m", "wind_direction_10m"]
FIELDS = ["city", "req_lat", "req_lon", "grid_lat", "grid_lon",
          "time_utc"] + VARS + ["fetched_at"]


def main():
    now = datetime.now(timezone.utc)
    last_full_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    cutoff = last_full_hour.strftime("%Y-%m-%dT%H:%M")  # buang jam prakiraan
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "latitude": ",".join(str(c[1]) for c in CITIES),
        "longitude": ",".join(str(c[2]) for c in CITIES),
        "hourly": ",".join(VARS),
        "past_days": 3,
        "forecast_days": 1,
    }
    resp = requests.get(URL, params=params, timeout=60)
    resp.raise_for_status()
    results = resp.json()
    assert len(results) == len(CITIES), "jumlah blok tidak sama dengan jumlah kota"

    # data lama dibaca dulu, baris baru menimpa baris dengan kunci sama
    rows = {}
    if os.path.exists(OUT):
        with open(OUT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["city"], r["time_utc"])] = r

    new_count = 0
    for (name, lat, lon), res in zip(CITIES, results):
        h = res["hourly"]
        for i, t in enumerate(h["time"]):
            if t > cutoff:
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

    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    print(f"baris diproses: {new_count}, total di file: {len(rows)}, cutoff: {cutoff}")


if __name__ == "__main__":
    main()
