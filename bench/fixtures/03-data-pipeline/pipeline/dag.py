"""The run: ingest -> validate (bad rows to quarantine) -> dedupe -> enrich -> load."""
from __future__ import annotations

import argparse
import sys

from .dedupe import drop_duplicates
from .enrich import add_region, load_regions
from .ingest import read_exports
from .load import write_jsonl
from .validate import check_row


def load_config(path: str) -> dict:
    cfg = {}
    for line in open(path, encoding="utf-8"):
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            cfg[k.strip()] = [x.strip() for x in v.strip("[]").split(",")] if v.startswith("[") else v
    return cfg


def run(cfg: dict, date: str) -> dict:
    good, bad = [], []
    for row, src in read_exports(cfg["source_dir"], date):
        reason = check_row(row, cfg["required_columns"], cfg["currencies"])
        if reason:
            bad.append(dict(row, _reason=reason, _source=src))
        else:
            good.append(row)
    unique, dstats = drop_duplicates(good)
    enriched = add_region(unique, load_regions(cfg["regions_file"]))
    written = write_jsonl(enriched, cfg["output_dir"], date, "orders")
    quarantined = write_jsonl(bad, cfg["quarantine_dir"], date, "rejected")
    return {"written": written, "quarantined": quarantined, "duplicates": dstats["dropped"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    print(run(load_config(a.config), a.date))
    return 0


if __name__ == "__main__":
    sys.exit(main())
