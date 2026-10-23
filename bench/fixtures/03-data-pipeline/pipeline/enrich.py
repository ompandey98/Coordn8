"""Add the customer's region from a lookup file. An unknown customer gets region 'unknown'."""
from __future__ import annotations

import csv


def load_regions(path: str) -> dict:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["customer_id"]: r["region"] for r in csv.DictReader(fh)}


def add_region(rows, regions: dict):
    for row in rows:
        out = dict(row)
        out["region"] = regions.get(row["customer_id"], "unknown")
        out["amount"] = round(float(row["amount"]), 2)
        yield out
