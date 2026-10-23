# shortlink

A small HTTP API that turns long URLs into short codes. It uses only the Python standard
library: `http.server` for transport and `sqlite3` for storage.

| Method | Path | What it does |
|---|---|---|
| POST | `/links` | create a short code for a URL (needs an API token) |
| GET | `/l/<code>` | redirect to the stored URL |
| GET | `/links/<code>/stats` | hit count for one code (needs an API token) |

Run it with `python -m shortlink.app --db links.db --port 8080`.
