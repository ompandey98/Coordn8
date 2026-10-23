from pipeline.dedupe import drop_duplicates


def test_keeps_first_copy():
    rows, stats = drop_duplicates([{"order_id": "A"}, {"order_id": "A"}, {"order_id": "B"}])
    assert [r["order_id"] for r in rows] == ["A", "B"] and stats["dropped"] == 1
