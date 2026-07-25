"""http — the shim's own contracts (no network).

Retries, backoff, redirect walking, charset resolution, and validator handling
belong to ``pf_core.fetch`` and are pinned by its test_fetch.py. Pinned here is
what pagespring adds: the PAGESPRING_UA identity, the ``timeout=`` keyword it
translates, the raw exceptions its patterns branch on, and the polite delay.
Requests are intercepted at the fetch core's ``_open`` seam.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from email.message import Message

import pytest
from pf_core.exceptions import InvalidInputError
from pf_core.fetch import Fetcher

from pagespring import http

URL = "https://docs.example.com/manual"
_ETAG = '"abc123"'
_LAST_MODIFIED = "Sat, 18 Jul 2026 10:00:00 GMT"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """These hosts never resolve, so skip the SSRF address check (the scheme check
    still runs; TestSsrfGuard exercises the guard itself). UA vars start unset."""
    monkeypatch.setenv("URL_FETCH_ALLOW_PRIVATE", "1")
    monkeypatch.delenv("PAGESPRING_UA", raising=False)
    monkeypatch.delenv("PF_FETCH_UA", raising=False)


def _message(items: dict[str, str] | None = None) -> Message:
    message = Message()
    for key, value in (items or {}).items():
        message[key] = value
    return message


class _Resp:
    """Stand-in for what the fetch core's ``_open`` returns."""

    def __init__(self, body: bytes = b"ok", headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = _message(headers)

    def read(self, amt: int | None = None) -> bytes:
        take = len(self._body) if amt is None else amt
        chunk, self._body = self._body[:take], self._body[take:]
        return chunk

    def close(self) -> None:
        pass


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, f"status {code}", _message(headers), None)


class _Seam:
    """The patched request seam: queue what ``_open`` serves, inspect what it got."""

    def __init__(self) -> None:
        self.calls: list[tuple[urllib.request.Request, float]] = []
        self.queue: list[object] = []

    def headers_sent(self, index: int = 0) -> dict[str, str]:
        return {key.lower(): value for key, value in self.calls[index][0].header_items()}

    def timeouts(self) -> list[float]:
        return [timeout for _request, timeout in self.calls]


@pytest.fixture()
def seam(monkeypatch) -> _Seam:
    """Intercept every request at ``Fetcher._open``; unqueued calls get a 200."""
    recorder = _Seam()

    def fake_open(_self, request, timeout_s):
        recorder.calls.append((request, timeout_s))
        item = recorder.queue.pop(0) if recorder.queue else _Resp()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(Fetcher, "_open", fake_open)
    return recorder


class TestUserAgent:
    def test_default_identifies_pagespring(self, seam):
        http.fetch_text(URL)
        agent = seam.headers_sent()["user-agent"]
        assert agent.startswith("pagespring/")
        assert "github.com/phierceweb/pagespring" in agent

    def test_env_override_wins(self, seam, monkeypatch):
        monkeypatch.setenv("PAGESPRING_UA", "custom-agent/1.0")
        http.fetch_text(URL)
        assert seam.headers_sent()["user-agent"] == "custom-agent/1.0"

    def test_empty_env_falls_back_to_default(self, seam, monkeypatch):
        monkeypatch.setenv("PAGESPRING_UA", "")
        http.fetch_text(URL)
        assert seam.headers_sent()["user-agent"].startswith("pagespring/")

    def test_framework_ua_env_does_not_win(self, seam, monkeypatch):
        """PAGESPRING_UA is the only knob: the shim passes its UA explicitly, which
        outranks the fetch core's own env var."""
        monkeypatch.setenv("PF_FETCH_UA", "pf-core-agent/9.9")
        http.fetch_text(URL)
        assert seam.headers_sent()["user-agent"].startswith("pagespring/")


class TestDelegation:
    def test_timeout_keyword_translated(self, seam):
        http.fetch_text(URL)
        http.fetch_text(URL, timeout=5)
        http.fetch_bytes(URL)
        http.fetch_bytes(URL, timeout=7)
        http.fetch_bytes_meta(URL)
        assert seam.timeouts() == [30, 5, 180, 7, 180]

    def test_final_url_is_the_last_hop(self, seam):
        """Patterns stamp the deliverable with the URL they actually fetched."""
        seam.queue.extend(
            [
                _http_error(301, {"Location": "https://docs.example.com/v2/manual"}),
                _Resp(b"body"),
            ]
        )
        final, text = http.fetch_text(URL)
        assert final == "https://docs.example.com/v2/manual"
        assert text == "body"

    def test_retries_forwarded(self, seam):
        seam.queue.append(_http_error(500))
        with pytest.raises(urllib.error.HTTPError):
            http.fetch_text(URL, retries=0)
        assert len(seam.calls) == 1  # retries=0 → one attempt, no backoff

    def test_http_error_propagates_raw(self, seam):
        """Patterns branch on ``.code`` (readthedocs' 404 PDF fallback, the
        microsoft_support 403 cooldown), so the shim must not wrap it."""
        seam.queue.append(_http_error(404))
        with pytest.raises(urllib.error.HTTPError) as raised:
            http.fetch_text(URL)
        assert raised.value.code == 404
        assert len(seam.calls) == 1  # a permanent client error is not retried

    def test_fetch_bytes_meta_returns_validators(self, seam):
        seam.queue.append(_Resp(b"%PDF", {"ETag": _ETAG, "Last-Modified": _LAST_MODIFIED}))
        final, data, meta = http.fetch_bytes_meta(URL)
        assert (final, data) == (URL, b"%PDF")
        assert meta == {"etag": _ETAG, "last_modified": _LAST_MODIFIED}

    def test_not_modified_true_on_304_and_sends_validators(self, seam):
        seam.queue.append(_http_error(304))
        assert http.not_modified(URL, etag=_ETAG, last_modified=_LAST_MODIFIED) is True
        sent = seam.headers_sent()
        assert sent["if-none-match"] == _ETAG
        assert sent["if-modified-since"] == _LAST_MODIFIED

    def test_not_modified_false_without_validators_and_no_request(self, seam):
        assert http.not_modified(URL, etag=None, last_modified=None) is False
        assert seam.calls == []  # nothing to probe with — no network


class TestSsrfGuard:
    def test_private_url_blocked_before_any_request(self, seam, monkeypatch):
        """Guarded by default; InvalidInputError is a FlowException the CLI reports."""
        monkeypatch.delenv("URL_FETCH_ALLOW_PRIVATE", raising=False)
        with pytest.raises(InvalidInputError):
            http.fetch_text("http://127.0.0.1:9/manual")
        assert seam.calls == []


class TestPoliteSleep:
    def test_sleeps_the_default_then_the_asked_interval(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", slept.append)
        http.polite_sleep()
        http.polite_sleep(1.0)
        assert slept == [0.25, 1.0]
