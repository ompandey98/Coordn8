"""Exports overlap at midnight, so the same order can arrive twice. Keep the first copy."""
from __future__ import annotations


def drop_duplicates(rows, key: str = "order_id"):
    """Yield rows whose key has not been seen before; count the dropped ones."""
    seen = set()
    stats = {"dropped": 0}

    def gen():
        for row in rows:
            k = row[key]
            if k in seen:
                stats["dropped"] += 1
                continue
            seen.add(k)
            yield row

    return gen(), stats
