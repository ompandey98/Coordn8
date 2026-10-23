from shortlink.validate import parse_new_link


def test_rejects_ftp():
    url, reason = parse_new_link(b'{"url": "ftp://example.org/x"}')
    assert url is None and "http" in reason
