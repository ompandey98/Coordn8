from pipeline.validate import check_row

REQ = ["order_id", "customer_id", "amount", "currency", "created_at"]


def test_negative_amount_is_rejected():
    row = {"order_id": "A", "customer_id": "C", "amount": "-1", "currency": "EUR", "created_at": "2026-03-01T00:00:00"}
    assert check_row(row, REQ, ["EUR"]) == "amount must be positive"
