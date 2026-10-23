"""Input checks for a new link. Every rule returns a reason the caller can show."""
from __future__ import annotations

import json
from urllib.parse import urlparse

MAX_URL = 2048


def parse_new_link(body: bytes):
    """Return (url, None) for a valid body, or (None, reason)."""
    try:
        doc = json.loads(body or b"{}")
    except ValueError:
        return None, "body is not JSON"
    url = doc.get("url") if isinstance(doc, dict) else None
    if not isinstance(url, str) or not url:
        return None, "missing 'url'"
    if len(url) > MAX_URL:
        return None, f"url longer than {MAX_URL} characters"
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, "url must be http or https with a host"
    return url, None
