"""Count entries by one key. The order of the result is stable: highest count first."""
from __future__ import annotations

from collections import Counter


def tally(entries, key: str = "level"):
    """Return [(value, count), ...] sorted by count (desc), then value (asc)."""
    if key not in ("level", "component"):
        raise ValueError(f"cannot tally by {key!r}")
    counts = Counter(getattr(e, key) for e in entries)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
