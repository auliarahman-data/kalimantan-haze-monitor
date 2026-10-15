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
URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
OUT = "data/air_quality.csv"
FIELDS = ["city", "req_lat", "req_lon", "grid_lat", "grid_lon",
          "time_utc", "pm2_5", "pm10", "us_aqi_pm2_5", "fetched_at"]


def main():
    now = datetime.now(timezone.utc)
    last_full_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    cutoff = last_full_hour.strftime("%Y-%m-%dT%H:%M")  # jam yang sudah lengkap
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "latitude": ",".join(str(c[1]) for c in CITIES),
        "longitude": ",".join(str(c[2]) for c in CITIES),
        "hourly": "pm2_5,pm10,us_aqi_pm2_5",
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
        for t, a, b, c in zip(h["time"], h["pm2_5"], h["pm10"], h["us_aqi_pm2_5"]):
            if t > cutoff:  # buang prakiraan
                continue
            rows[(name, t)] = {
                "city": name, "req_lat": lat, "req_lon": lon,
                "grid_lat": res["latitude"], "grid_lon": res["longitude"],
                "time_utc": t, "pm2_5": a, "pm10": b, "us_aqi_pm2_5": c,
                "fetched_at": fetched_at,
            }
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
