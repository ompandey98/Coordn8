"""Build one predicate from the command-line options, so the pipeline filters in one pass."""
from __future__ import annotations

LEVELS = ("debug", "info", "warn", "error")


def level_rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def build_filter(level=None, since=None):
    """Return keep(entry) -> bool combining the level floor and the time window."""
    floor = level_rank(level.lower()) if level else 0

    def keep(entry) -> bool:
        if level_rank(entry.level) < floor:
            return False
        if since is not None and entry.ts < since:
            return False
        return True

    return keep
