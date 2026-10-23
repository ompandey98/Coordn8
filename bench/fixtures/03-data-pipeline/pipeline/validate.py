"""Row checks. A row either passes every check or goes to quarantine with the first reason."""
from __future__ import annotations

from datetime import datetime


def check_row(row: dict, required, currencies):
    """Return None for a valid row, or a short reason string."""
    for col in required:
        if not (row.get(col) or "").strip():
            return f"missing {col}"
    try:
        amount = float(row["amount"])
    except ValueError:
        return "amount is not a number"
    if amount <= 0:
        return "amount must be positive"
    if row["currency"] not in currencies:
        return f"unknown currency {row['currency']}"
    try:
        datetime.fromisoformat(row["created_at"])
    except ValueError:
        return "created_at is not an ISO timestamp"
    return None
