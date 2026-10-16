#!/usr/bin/env python3
"""Load Kalimantan province boundaries into BigQuery (one-off, manual run).

Source: geoBoundaries gbOpen IDN ADM1, pinned to a commit so the result is
reproducible. Underlying data: OpenStreetMap contributors (ODbL 1.0).
The boundary file is downloaded at run time and is NOT committed to the repo.

Keeps only the five Kalimantan provinces. If the number of provinces found is
not exactly 5, the script stops with an error instead of truncating the table.
Geometry is stored as GeoJSON text; a BigQuery view converts it to GEOGRAPHY.

Env vars: BQ_PROJECT (required), BQ_DATASET (default "haze"),
BQ_PROVINCE_TABLE (default "kalimantan_provinces").
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from google.cloud import bigquery

COMMIT = "9469f09"
PATH = "releaseData/gbOpen/IDN/ADM1/geoBoundaries-IDN-ADM1.geojson"
URLS = [
    f"https://raw.githubusercontent.com/wmgeolab/geoBoundaries/{COMMIT}/{PATH}",
    f"https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/{COMMIT}/{PATH}",
]
SOURCE = "geoBoundaries gbOpen IDN ADM1 (OpenStreetMap contributors, ODbL 1.0)"
EXPECTED = 5

SCHEMA = [
    bigquery.SchemaField("province_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("shape_id", "STRING"),
    bigquery.SchemaField("shape_iso", "STRING"),
    bigquery.SchemaField("geometry_type", "STRING"),
    bigquery.SchemaField("geojson", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("loaded_at", "TIMESTAMP"),
]


def download() -> dict:
    for url in URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kalimantan-haze-monitor"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
        except Exception as exc:  # try the next URL
            print(f"download failed: {url} -> {exc}", file=sys.stderr)
            continue
        if raw.startswith(b"version https://git-lfs"):
            print(f"got a Git LFS pointer from {url}; trying next URL", file=sys.stderr)
            continue
        print(f"downloaded {len(raw) / 1e6:.2f} MB from {url}")
        return json.loads(raw)
    raise RuntimeError("could not download the boundary file from any URL")


def main() -> int:
    data = download()
    features = data.get("features", [])
    print(f"features in file: {len(features)}")

    loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for ft in features:
        props = ft.get("properties", {})
        name = props.get("shapeName", "")
        if not name.lower().endswith("kalimantan"):
            continue
        geom = ft["geometry"]
        rows.append({
            "province_name": name,
            "shape_id": props.get("shapeID"),
            "shape_iso": props.get("shapeISO"),
            "geometry_type": geom.get("type"),
            "geojson": json.dumps(geom, separators=(",", ":")),
            "source": SOURCE,
            "loaded_at": loaded_at,
        })

    if len(rows) != EXPECTED:
        print(f"expected {EXPECTED} Kalimantan provinces, found {len(rows)}; "
              "refusing to truncate the table", file=sys.stderr)
        print("all province names in file:", file=sys.stderr)
        for ft in features:
            print("  ", ft.get("properties", {}).get("shapeName"), file=sys.stderr)
        return 1

    for r in rows:
        print(f"{r['province_name']} | {r['geometry_type']} | {len(r['geojson']) / 1e3:.0f} KB")

    project = os.environ["BQ_PROJECT"]
    dataset = os.environ.get("BQ_DATASET", "haze")
    table = os.environ.get("BQ_PROVINCE_TABLE", "kalimantan_provinces")
    table_id = f"{project}.{dataset}.{table}"

    client = bigquery.Client(project=project)
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result()

    loaded = client.get_table(table_id).num_rows
    print(f"loaded into {table_id} | rows sent: {len(rows)} | table rows: {loaded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
