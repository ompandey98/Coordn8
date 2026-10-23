"""Server entry point: one handler class dispatches every request through the same chain."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .auth import authenticate
from .ratelimit import RateLimiter
from .routes import dispatch
from .store import Store


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    limiter: RateLimiter = None

    def _handle(self, method: str) -> None:
        owner = authenticate(self.headers.get("Authorization"), self.store)
        client = owner or self.client_address[0]
        if not self.limiter.allow(client):
            return self._send(429, {"error": "rate limit exceeded"})
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, payload, headers = dispatch(method, self.path, body, owner, self.store)
        self._send(status, payload, headers)

    def _send(self, status, payload, headers=None):
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="links.db")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--rpm", type=int, default=60)
    a = ap.parse_args(argv)
    Handler.store = Store(a.db)
    Handler.limiter = RateLimiter(per_minute=a.rpm)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
