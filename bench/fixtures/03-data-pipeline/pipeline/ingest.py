"""Read every CSV export for one date as dict rows, tagged with the file they came from."""
from __future__ import annotations

import csv
from pathlib import Path


def read_exports(source_dir: str, date: str):
    """Yield (row, source_name) for each row in <source_dir>/<date>*.csv, in file order."""
    for path in sorted(Path(source_dir).glob(f"{date}*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                yield row, path.name
