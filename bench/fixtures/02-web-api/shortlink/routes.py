"""Routing: one table maps (method, path pattern) to a handler function."""
from __future__ import annotations

import re

from .validate import parse_new_link


def create_link(m, body, owner, store):
    if owner is None:
        return 401, {"error": "token required"}, None
    url, reason = parse_new_link(body)
    if reason:
        return 400, {"error": reason}, None
    code = store.create(url, owner)
    return 201, {"code": code, "short": f"/l/{code}"}, None


def follow(m, body, owner, store):
    url = store.resolve(m.group("code"))
    if url is None:
        return 404, {"error": "unknown code"}, None
    return 302, None, {"Location": url}


def stats(m, body, owner, store):
    if owner is None:
        return 401, {"error": "token required"}, None
    hits = store.stats(m.group("code"), owner)
    if hits is None:
        return 404, {"error": "unknown code"}, None
    return 200, {"code": m.group("code"), "hits": hits}, None


ROUTES = [
    ("POST", re.compile(r"^/links$"), create_link),
    ("GET", re.compile(r"^/l/(?P<code>[a-z2-9]{7})$"), follow),
    ("GET", re.compile(r"^/links/(?P<code>[a-z2-9]{7})/stats$"), stats),
]


def dispatch(method, path, body, owner, store):
    for meth, pattern, fn in ROUTES:
        m = pattern.match(path)
        if m and meth == method:
            return fn(m, body, owner, store)
    return 404, {"error": "no such route"}, None
