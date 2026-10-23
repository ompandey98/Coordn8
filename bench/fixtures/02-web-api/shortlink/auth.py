"""Bearer-token authentication. Anonymous callers may only follow redirects."""
from __future__ import annotations


def authenticate(header, store):
    """Return the owner for a valid 'Bearer <token>' header, else None."""
    if not header or not header.startswith("Bearer "):
        return None
    return store.owner_for_token(header[len("Bearer "):].strip())
