from shortlink.routes import dispatch


class FakeStore:
    def create(self, url, owner):
        return "abc2345"

    def resolve(self, code):
        return "https://example.org/a" if code == "abc2345" else None


def test_create_needs_token():
    status, _, _ = dispatch("POST", "/links", b'{"url": "https://example.org"}', None, FakeStore())
    assert status == 401


def test_follow_redirects():
    status, _, headers = dispatch("GET", "/l/abc2345", b"", None, FakeStore())
    assert status == 302 and headers["Location"].startswith("https://")
