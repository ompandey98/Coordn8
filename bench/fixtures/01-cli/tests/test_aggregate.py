from logtally.aggregate import tally
from logtally.parse import parse_line


def test_tally_orders_by_count():
    lines = ["2026-03-01T10:00:00 INFO [api] ok", "2026-03-01T10:00:01 INFO [api] ok",
             "2026-03-01T10:00:02 ERROR [db] down"]
    rows = tally([parse_line(x) for x in lines], key="level")
    assert rows == [("info", 2), ("error", 1)]
