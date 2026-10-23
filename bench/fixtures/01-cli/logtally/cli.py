"""Command-line entry point: parse arguments, run the pipeline, choose an exit status."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .aggregate import tally
from .filters import build_filter
from .parse import parse_file
from .render import render_json, render_table

EXIT_OK = 0
EXIT_SKIPPED = 2
EXIT_USAGE = 64


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logtally", description="Count log entries.")
    p.add_argument("files", nargs="+", help="log files to read")
    p.add_argument("--level", default=None, help="lowest level to keep (debug, info, warn, error)")
    p.add_argument("--since", default=None, help="ISO timestamp; drop older entries")
    p.add_argument("--by", choices=("level", "component"), default="level")
    p.add_argument("--format", choices=("table", "json"), default="table")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        since = datetime.fromisoformat(args.since) if args.since else None
    except ValueError:
        sys.stderr.write(f"logtally: --since is not an ISO timestamp: {args.since}\n")
        return EXIT_USAGE
    keep = build_filter(level=args.level, since=since)
    entries, skipped = [], 0
    for path in args.files:
        got, bad = parse_file(path)
        entries.extend(e for e in got if keep(e))
        skipped += bad
    counts = tally(entries, key=args.by)
    out = render_json(counts) if args.format == "json" else render_table(counts, key=args.by)
    sys.stdout.write(out)
    if skipped:
        sys.stderr.write(f"logtally: skipped {skipped} malformed line(s)\n")
        return EXIT_SKIPPED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
