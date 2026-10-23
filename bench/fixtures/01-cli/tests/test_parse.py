from logtally.parse import parse_line


def test_valid_line():
    e = parse_line("2026-03-01T10:00:00 WARN [db.pool] slow checkout 420ms")
    assert e.level == "warn" and e.component == "db.pool"


def test_malformed_line_is_none():
    assert parse_line("not a log line") is None
