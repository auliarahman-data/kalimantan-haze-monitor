#!/usr/bin/env python3
"""Create or replace every analysis view from sql/views.sql in BigQuery.

The views are kept as code in this repository. After a sandbox expiry or a
change to sql/views.sql, running this script (or the apply-views workflow)
rebuilds them; no manual copy-paste into the console.

The file is run as one BigQuery script, so the views are created in file
order. Project ID comes from BQ_PROJECT; if it differs from the project
named in the file, the name is swapped before running.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from google.cloud import bigquery

SQL_PATH = Path("sql/views.sql")
FILE_PROJECT = "kalimantan-haze"


def main() -> int:
    project = os.environ["BQ_PROJECT"]
    sql = SQL_PATH.read_text(encoding="utf-8")
    if project != FILE_PROJECT:
        sql = sql.replace(f"`{FILE_PROJECT}.", f"`{project}.")
    views = re.findall(r"CREATE OR REPLACE VIEW `[^`]+\.([^`.]+)`", sql)
    if not views:
        print("no CREATE OR REPLACE VIEW statements found", file=sys.stderr)
        return 1

    client = bigquery.Client(project=project)
    client.query(sql).result()   # raises on the first failing statement

    dataset = os.environ.get("BQ_DATASET", "haze")
    for name in views:
        table = client.get_table(f"{project}.{dataset}.{name}")
        print(f"ok  {name}  ({len(table.schema)} columns)")
    print(f"{len(views)} views applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
