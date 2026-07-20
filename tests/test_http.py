"""http — status-aware retries, Retry-After, and charset handling (no network).

urlopen is faked with a response/exception queue; time.sleep is recorded, so
retry and backoff behavior is asserted without real waiting.
"""

import urllib.error
import urllib.request
from email.message import Message

import pytest

from pagespring import http


class _Resp:
    def __init__(self, body=b"ok", url="https://x/final", charset=None):
        self._body = body
        self._url = url
        self.headers = Message()
        self.headers["Content-Type"] = f"text/html; charset={charset}" if charset else "text/html"

    def geturl(self):
        return self._url

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x", code, "boom", headers, None)


@pytest.fixture()
def fake_net(monkeypatch):
    """(queue, calls, sleeps): queued responses/exceptions, URLs fetched, sleeps taken."""
    queue: list = []
    calls: list = []
    sleeps: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    return queue, calls, sleeps


def test_permanent_4xx_fails_immediately(fake_net):
    queue, calls, _ = fake_net
    queue.append(_http_error(404))
    with pytest.raises(urllib.error.HTTPError):
        http.fetch_text("https://x/a")
    assert len(calls) == 1  # a permanent client error must not be retried


def test_5xx_retries_then_succeeds(fake_net):
    queue, calls, sleeps = fake_net
    queue.extend([_http_error(500), _http_error(503), _Resp(b"recovered")])
    _f, text = http.fetch_text("https://x/a")
    assert text == "recovered"
    assert len(calls) == 3
    assert len(sleeps) == 2  # backoff between attempts


def test_429_honors_retry_after(fake_net):
    queue, calls, sleeps = fake_net
    queue.extend([_http_error(429, retry_after=3), _Resp(b"ok")])
    http.fetch_text("https://x/a")
    assert len(calls) == 2
    assert sleeps == [3.0]  # waited what the server asked, not the default backoff


def test_429_retry_after_capped_at_30s(fake_net):
    queue, _, sleeps = fake_net
    queue.extend([_http_error(429, retry_after=600), _Resp(b"ok")])
    http.fetch_text("https://x/a")
    assert sleeps == [30.0]


def test_charset_from_content_type_header(fake_net):
    queue, _, _ = fake_net
    queue.append(_Resp("café".encode("latin-1"), charset="iso-8859-1"))
    _f, text = http.fetch_text("https://x/a")
    assert text == "café"


def test_explicit_encoding_overrides_header(fake_net):
    queue, _, _ = fake_net
    queue.append(_Resp("café".encode("latin-1"), charset="utf-8"))  # header lies
    _f, text = http.fetch_text("https://x/a", encoding="latin-1")
    assert text == "café"


def test_url_error_still_retries(fake_net):
    queue, calls, _ = fake_net
    queue.extend([urllib.error.URLError("timeout"), _Resp(b"ok")])
    _f, text = http.fetch_text("https://x/a")
    assert text == "ok"
    assert len(calls) == 2


def test_fetch_bytes_meta_returns_validators(fake_net):
    queue, _, _ = fake_net
    resp = _Resp(b"pdf-bytes")
    resp.headers["ETag"] = '"abc123"'
    resp.headers["Last-Modified"] = "Sat, 18 Jul 2026 10:00:00 GMT"
    queue.append(resp)

    final, data, meta = http.fetch_bytes_meta("https://x/manual.pdf")

    assert data == b"pdf-bytes"
    assert meta["etag"] == '"abc123"'
    assert meta["last_modified"] == "Sat, 18 Jul 2026 10:00:00 GMT"


def test_fetch_bytes_meta_absent_validators_are_none(fake_net):
    queue, _, _ = fake_net
    queue.append(_Resp(b"pdf-bytes"))
    _f, _d, meta = http.fetch_bytes_meta("https://x/manual.pdf")
    assert meta == {"etag": None, "last_modified": None}


def test_not_modified_true_on_304_and_sends_validators(fake_net, monkeypatch):
    queue, _, _ = fake_net
    sent: dict = {}

    def fake_urlopen(req, timeout=None):
        sent.update(dict(req.header_items()))
        raise _http_error(304)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert (
        http.not_modified(
            "https://x/manual.pdf",
            etag='"abc123"',
            last_modified="Sat, 18 Jul 2026 10:00:00 GMT",
        )
        is True
    )
    assert sent["If-none-match"] == '"abc123"'
    assert sent["If-modified-since"] == "Sat, 18 Jul 2026 10:00:00 GMT"


def test_not_modified_false_on_200(fake_net):
    queue, _, _ = fake_net
    queue.append(_Resp(b"changed content"))
    assert http.not_modified("https://x/manual.pdf", etag='"abc123"', last_modified=None) is False


def test_not_modified_false_without_validators_and_no_request(fake_net):
    _queue, calls, _ = fake_net
    assert http.not_modified("https://x/manual.pdf", etag=None, last_modified=None) is False
    assert calls == []  # nothing to probe with — no network


def test_not_modified_false_on_network_error(fake_net):
    queue, _, _ = fake_net
    queue.append(urllib.error.URLError("timeout"))
    assert http.not_modified("https://x/manual.pdf", etag='"abc"', last_modified=None) is False


def test_default_ua_identifies_pagespring(monkeypatch):
    monkeypatch.delenv("PAGESPRING_UA", raising=False)
    ua = http._request("https://x/a").get_header("User-agent")
    assert ua.startswith("pagespring/")
    assert "github.com" in ua


def test_ua_env_override(monkeypatch):
    monkeypatch.setenv("PAGESPRING_UA", "custom-agent/1.0")
    assert http._request("https://x/a").get_header("User-agent") == "custom-agent/1.0"
