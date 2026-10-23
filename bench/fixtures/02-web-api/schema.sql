CREATE TABLE IF NOT EXISTS links (
  code       TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  owner      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  hits       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  owner TEXT NOT NULL
);
