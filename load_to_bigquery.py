#!/usr/bin/env python3
"""Load data/fire_hotspots/*.csv (one file per month) into BigQuery (full reload).

The BigQuery sandbox has no DML (no MERGE/UPDATE/DELETE) and tables expire
after 60 days, so the table is rebuilt from the CSV on every run with a load
job using WRITE_TRUNCATE. The CSV in the repo is the source of truth.

Safety: if there are no monthly CSVs or they have no data rows, the script stops with an
error instead of truncating the table.

Authentication: Application Default Credentials (set up in the workflow by
google-github-actions/auth). Settings come from environment variables:
BQ_PROJECT (required), BQ_DATASET (default "haze"), BQ_TABLE (default
"fire_hotspots").
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

CSV_DIR = Path("data/fire_hotspots")

SCHEMA = [
    bigquery.SchemaField("detected_at_utc", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("acq_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("latitude", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("longitude", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("bright_ti4", "FLOAT64"),
    bigquery.SchemaField("bright_ti5", "FLOAT64"),
    bigquery.SchemaField("scan", "FLOAT64"),
    bigquery.SchemaField("track", "FLOAT64"),
    bigquery.SchemaField("satellite", "STRING"),
    bigquery.SchemaField("instrument", "STRING"),
    bigquery.SchemaField("confidence", "STRING"),
    bigquery.SchemaField("frp", "FLOAT64"),
    bigquery.SchemaField("daynight", "STRING"),
    bigquery.SchemaField("version", "STRING"),
    bigquery.SchemaField("source", "STRING"),   # VIIRS_SNPP_NRT or VIIRS_SNPP_SP
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]
# CSV column order that matches SCHEMA
COLUMNS = [f.name for f in SCHEMA]


def prepare_upload(srcs: list[Path], dst: Path, ingested_at: str) -> int:
    """Concatenate srcs into dst in SCHEMA column order, adding ingested_at.
    Returns row count. Fails on a duplicate key across files."""
    count = 0
    seen = set()
    with dst.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(COLUMNS)
        for src in srcs:
            with src.open(newline="", encoding="utf-8") as fin:
                for row in csv.DictReader(fin):
                    key = (row["detected_at_utc"], row["latitude"], row["longitude"])
                    if key in seen:
                        raise ValueError(f"duplicate detection {key} in {src}")
                    seen.add(key)
                    row["ingested_at"] = ingested_at
                    row["source"] = row.get("source") or "VIIRS_SNPP_NRT"
                    writer.writerow([row[c] for c in COLUMNS])
                    count += 1
    return count


def main() -> int:
    srcs = sorted(CSV_DIR.glob("*.csv"))
    if not srcs:
        print(f"no CSVs in {CSV_DIR}; refusing to truncate the table", file=sys.stderr)
        return 1

    project = os.environ["BQ_PROJECT"]
    dataset = os.environ.get("BQ_DATASET", "haze")
    table = os.environ.get("BQ_TABLE", "fire_hotspots")
    table_id = f"{project}.{dataset}.{table}"

    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with tempfile.TemporaryDirectory() as tmp:
        upload = Path(tmp) / "upload.csv"
        rows = prepare_upload(srcs, upload, ingested_at)
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
        job.result()  # waits; raises on failure

    loaded = client.get_table(table_id).num_rows
    print(f"loaded into {table_id} | files: {len(srcs)} | csv rows: {rows} | table rows: {loaded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
