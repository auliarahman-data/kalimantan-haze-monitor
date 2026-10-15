#!/usr/bin/env python3
"""Load data/weather_hourly.csv into BigQuery (full reload).

Same approach as load_air_quality.py: the sandbox has no DML, so the table
is rebuilt from the CSV on every run with WRITE_TRUNCATE. The CSV in the
repo is the source of truth. If the CSV is missing or empty, the script
stops with an error instead of truncating the table.

Env vars: BQ_PROJECT (required), BQ_DATASET (default "haze"),
BQ_WEATHER_TABLE (default "weather_hourly").
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

from google.cloud import bigquery

CSV_PATH = Path("data/weather_hourly.csv")

SCHEMA = [
    bigquery.SchemaField("city", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("req_lat", "FLOAT64"),
    bigquery.SchemaField("req_lon", "FLOAT64"),
    bigquery.SchemaField("grid_lat", "FLOAT64"),
    bigquery.SchemaField("grid_lon", "FLOAT64"),
    bigquery.SchemaField("time_utc", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("temperature_2m", "FLOAT64"),
    bigquery.SchemaField("relative_humidity_2m", "FLOAT64"),
    bigquery.SchemaField("precipitation", "FLOAT64"),
    bigquery.SchemaField("cloud_cover", "FLOAT64"),
    bigquery.SchemaField("wind_speed_10m", "FLOAT64"),
    bigquery.SchemaField("wind_direction_10m", "FLOAT64"),
    bigquery.SchemaField("fetched_at", "TIMESTAMP"),
]
COLUMNS = [f.name for f in SCHEMA]


def to_ts(value: str) -> str:
    """'2026-10-12T00:00' or '2026-10-15T10:40:16Z' -> 'YYYY-MM-DD HH:MM:SS'."""
    if not value:
        return ""
    v = value.replace("T", " ").replace("Z", "")
    if len(v) == 16:  # no seconds
        v += ":00"
    return v


def prepare_upload(src: Path, dst: Path) -> int:
    count = 0
    with src.open(newline="", encoding="utf-8") as fin, \
            dst.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(COLUMNS)
        for row in csv.DictReader(fin):
            row["time_utc"] = to_ts(row["time_utc"])
            row["fetched_at"] = to_ts(row["fetched_at"])
            writer.writerow([row[c] for c in COLUMNS])
            count += 1
    return count


def main() -> int:
    if not CSV_PATH.exists():
        print(f"{CSV_PATH} not found; refusing to truncate the table", file=sys.stderr)
        return 1

    project = os.environ["BQ_PROJECT"]
    dataset = os.environ.get("BQ_DATASET", "haze")
    table = os.environ.get("BQ_WEATHER_TABLE", "weather_hourly")
    table_id = f"{project}.{dataset}.{table}"

    with tempfile.TemporaryDirectory() as tmp:
        upload = Path(tmp) / "upload.csv"
        rows = prepare_upload(CSV_PATH, upload)
        if rows == 0:
            print("CSV has no data rows; refusing to truncate the table", file=sys.stderr)
            return 1

        client = bigquery.Client(project=project)
        job_config = bigquery.LoadJobConfig(
            schema=SCHEMA,
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        with upload.open("rb") as f:
            job = client.load_table_from_file(f, table_id, job_config=job_config)
        job.result()

    loaded = client.get_table(table_id).num_rows
    print(f"loaded into {table_id} | csv rows: {rows} | table rows: {loaded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
