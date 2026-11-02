"""http: only what the shim adds over ``pw_core.fetch``, whose own tests pin retries, redirects and
charsets; requests are intercepted at ``Fetcher._open`` (no network)."""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request
from email.message import Message

import pytest
from pw_core.fetch import Fetcher
from pw_core.utils.http_tls import verify_tls
from pw_foundation.exceptions import InvalidInputError

from pagespring import http

URL = "https://docs.example.com/manual"
_ETAG = '"abc123"'
_LAST_MODIFIED = "Sat, 18 Jul 2026 10:00:00 GMT"
_TLS_OFF_ENV_VARS = ["PW_VERIFY_TLS", "URL_CHECK_VERIFY_TLS"]
# Deprecated spellings the framework may still honour; immunity must hold whether or not it does.
_DEPRECATED_TLS_OFF_ENV_VARS = ["PF_VERIFY_TLS"]


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """These hosts never resolve, so skip the SSRF address check (the scheme check
    still runs; TestSsrfGuard exercises the guard itself). Tunable vars start unset."""
    monkeypatch.setenv("URL_FETCH_ALLOW_PRIVATE", "1")
    for var in (
        "PAGESPRING_UA",
        "PW_FETCH_UA",
        "PF_FETCH_UA",
        "PAGESPRING_MAX_TEXT_BYTES",
        "PAGESPRING_MAX_DOWNLOAD_BYTES",
    ):
        monkeypatch.delenv(var, raising=False)


def _message(items: dict[str, str] | None = None) -> Message:
    message = Message()
    for key, value in (items or {}).items():
        message[key] = value
    return message


class _Resp:
    """Stand-in for what the fetch core's ``_open`` returns."""

    status = 200

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
        self.fetchers: list[Fetcher] = []

    def headers_sent(self, index: int = 0) -> dict[str, str]:
        return {key.lower(): value for key, value in self.calls[index][0].header_items()}

    def timeouts(self) -> list[float]:
        return [timeout for _request, timeout in self.calls]


@pytest.fixture()
def seam(monkeypatch) -> _Seam:
    """Intercept every request at ``Fetcher._open``; unqueued calls get a 200."""
    recorder = _Seam()

    def fake_open(fetcher, request, timeout_s):
        recorder.fetchers.append(fetcher)
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
        monkeypatch.setenv("PW_FETCH_UA", "pw-core-agent/9.9")
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

    def test_not_modified_true_on_200_with_the_strong_etag_it_sent(self, seam):
        seam.queue.append(_Resp(headers={"ETag": _ETAG}))
        assert http.not_modified(URL, etag=_ETAG, last_modified=None) is True

    def test_not_modified_false_without_validators_and_no_request(self, seam):
        assert http.not_modified(URL, etag=None, last_modified=None) is False
        assert seam.calls == []  # nothing to probe with — no network


@pytest.mark.real_fetch_body
class TestFetchBody:
    def test_keeps_bytes_charset_and_validators(self, seam):
        raw = "caf\u00e9".encode("latin-1")
        seam.queue.append(
            _Resp(raw, {"Content-Type": "text/html; charset=ISO-8859-1", "ETag": '"e"'})
        )
        body = http.fetch_body(URL)
        assert (body.data, body.charset) == (raw, "iso-8859-1")
        assert body.validators == {"etag": '"e"', "last_modified": None}
        assert body.text() == "caf\u00e9"

    def test_decodes_as_fetch_text_with_its_budget_and_timeout(self, seam):
        raw = b"\xe2\x9c\x93 ok \xff"
        seam.queue.extend([_Resp(raw), _Resp(raw)])
        assert http.fetch_body(URL).text() == http.fetch_text(URL)[1]
        assert seam.timeouts() == [30, 30]
        assert [f._max_bytes for f in seam.fetchers] == [http._text_max_bytes()] * 2


class TestSsrfGuard:
    def test_private_url_blocked_before_any_request(self, seam, monkeypatch):
        """Guarded by default; InvalidInputError is a FlowException the CLI reports."""
        monkeypatch.delenv("URL_FETCH_ALLOW_PRIVATE", raising=False)
        with pytest.raises(InvalidInputError):
            http.fetch_text("http://127.0.0.1:9/manual")
        assert seam.calls == []

    def test_allow_private_opts_the_process_out(self, seam, monkeypatch):
        """The guard is defeasible, unlike TLS verification."""
        monkeypatch.setenv("URL_FETCH_ALLOW_PRIVATE", "1")
        http.fetch_text("http://127.0.0.1:9/manual")
        assert len(seam.calls) == 1, "the opt-out did not let the request through"

    def test_non_http_scheme_blocked_even_with_allow_private(self, seam, monkeypatch):
        """Scheme enforcement survives the opt-out — file:// is never fetchable."""
        monkeypatch.setenv("URL_FETCH_ALLOW_PRIVATE", "1")
        with pytest.raises(InvalidInputError):
            http.fetch_text("file:///etc/passwd")
        assert seam.calls == []


def _tls_context(fetcher: Fetcher) -> ssl.SSLContext:
    """The context frozen into the fetcher's opener when it was built."""
    handler = next(
        h for h in fetcher._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)
    )
    return handler._context


class TestTlsVerification:
    @pytest.mark.parametrize("env_var", _TLS_OFF_ENV_VARS)
    def test_off_switch_is_really_honored_by_the_framework(self, env_var, monkeypatch):
        """Sanity check on the knob itself, so immunity can't be an artifact of an
        env var that does nothing."""
        monkeypatch.setenv(env_var, "0")
        assert verify_tls() is False

    @pytest.mark.filterwarnings("ignore:PF_VERIFY_TLS is deprecated:DeprecationWarning")
    @pytest.mark.parametrize("env_var", [*_TLS_OFF_ENV_VARS, *_DEPRECATED_TLS_OFF_ENV_VARS])
    def test_off_switch_cannot_disable_certificate_verification(self, seam, env_var, monkeypatch):
        """The fetch core's TLS switch is process-wide; one set for another consumer
        must not turn verification off for pagespring's fetches."""
        monkeypatch.setenv(env_var, "0")
        http.fetch_text(URL)
        http.fetch_bytes(URL)
        assert len(seam.fetchers) == 2
        for context in map(_tls_context, seam.fetchers):
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname is True


class TestPoliteSleep:
    def test_sleeps_the_default_then_the_asked_interval(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", slept.append)
        http.polite_sleep()
        http.polite_sleep(1.0)
        assert slept == [0.25, 1.0]


class TestFetchSizeCaps:
    """Every helper caps the fetcher it builds (pw-core's default is unlimited); asserted through
    the public helpers, since a cap one never passes is the bug."""

    def test_every_helper_caps_the_fetcher_it_builds(self, seam):
        seam.queue.extend([_Resp(), _Resp(), _Resp(), _http_error(304)])  # 304 lands on the last
        http.fetch_text(URL)
        http.fetch_bytes(URL)
        http.fetch_bytes_meta(URL)
        http.not_modified(URL, etag=_ETAG, last_modified=None)
        caps = [fetcher._max_bytes for fetcher in seam.fetchers]
        assert len(caps) == 4, f"a helper made no request: {caps}"
        assert all(cap is not None and cap > 0 for cap in caps), f"unbounded fetcher: {caps}"

    def test_binary_downloads_get_more_room_than_text(self, seam):
        http.fetch_text(URL)
        http.fetch_bytes(URL)
        text_cap, binary_cap = (fetcher._max_bytes for fetcher in seam.fetchers)
        assert binary_cap > text_cap

    def test_caps_are_env_tunable(self, seam, monkeypatch):
        monkeypatch.setenv("PAGESPRING_MAX_TEXT_BYTES", "1234")
        monkeypatch.setenv("PAGESPRING_MAX_DOWNLOAD_BYTES", "5678")
        http.fetch_text(URL)
        http.fetch_bytes(URL)
        assert [fetcher._max_bytes for fetcher in seam.fetchers] == [1234, 5678]

    @pytest.mark.parametrize("value", ["not-a-number", "", "0", "-1"])
    def test_unusable_env_value_falls_back_to_the_default(self, seam, monkeypatch, value):
        """A malformed or non-positive override must not uncap the fetch."""
        monkeypatch.setenv("PAGESPRING_MAX_TEXT_BYTES", value)
        http.fetch_text(URL)
        assert seam.fetchers[0]._max_bytes == http._TEXT_MAX_BYTES_DEFAULT


class TestPostForm:
    FIELDS = {"message": '{"actions":[]}', "aura.token": "null"}

    def test_sends_the_fields_urlencoded_as_a_post(self, seam):
        seam.queue.append(_Resp(b'{"ok":true}'))
        final_url, text = http.post_form(URL, self.FIELDS)
        request = seam.calls[0][0]
        assert request.get_method() == "POST"
        assert request.data == b"message=%7B%22actions%22%3A%5B%5D%7D&aura.token=null"
        assert seam.headers_sent()["content-type"].startswith("application/x-www-form-urlencoded")
        assert (final_url, text) == (URL, '{"ok":true}')

    def test_keeps_the_crawl_ua_and_the_text_cap(self, seam):
        http.post_form(URL, self.FIELDS)
        assert seam.headers_sent()["user-agent"].startswith("pagespring/")
        assert seam.fetchers[0]._max_bytes == http._TEXT_MAX_BYTES_DEFAULT

    def test_a_retry_resends_the_body(self, seam, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        seam.queue.extend([_http_error(503), _Resp()])
        http.post_form(URL, self.FIELDS)
        assert [request.data for request, _t in seam.calls] == [seam.calls[0][0].data] * 2
        assert seam.calls[1][0].get_method() == "POST"

    def test_a_redirect_is_raised_not_followed(self, seam):
        """Following would re-POST the form to wherever the Location points."""
        seam.queue.append(_http_error(302, {"Location": "https://elsewhere.example/x"}))
        with pytest.raises(urllib.error.HTTPError) as exc:
            http.post_form(URL, self.FIELDS)
        assert exc.value.code == 302
        assert len(seam.calls) == 1


class TestNonAsciiUrls:
    """urllib's request line is ASCII-only, so a URL is encoded the way a browser sends it."""

    @pytest.mark.parametrize(
        ("url", "sent"),
        [
            ("https://x.example/img/café.png", "https://x.example/img/caf%C3%A9.png"),
            ("https://x.example/a.png?v=1®=us", "https://x.example/a.png?v=1%C2%AE=us"),
            ("https://x.example/my img.png", "https://x.example/my%20img.png"),
            (
                "https://x.example/a%20b/ü?q=a/b&c=%2F#f",
                "https://x.example/a%20b/%C3%BC?q=a/b&c=%2F#f",
            ),
            ("https://bücher.example/a.png", "https://xn--bcher-kva.example/a.png"),
            ("https://bücher.example:8443/a.png", "https://xn--bcher-kva.example:8443/a.png"),
            ("https://bücher.EXAMPLE/a.png", "https://xn--bcher-kva.example/a.png"),
            ("https://\U0001f4a9.example/a.png", "https://xn--ls8h.example/a.png"),
            ("https://x.example/a\n.png", "https://x.example/a.png"),
            (" https://bü\tcher.example/a.png\r\n", "https://xn--bcher-kva.example/a.png"),
        ],
        ids=[
            "path",
            "query",
            "space",
            "already-escaped-kept",
            "idn-host",
            "idn-host-port",
            "idn-host-mixed-case",
            "idn-host-newer-symbol",
            "newline-removed",
            "tab-and-edges-removed",
        ],
    )
    def test_every_fetch_sends_an_ascii_url(self, seam, url, sent):
        for call in (
            http.fetch_text,
            http.fetch_body,
            http.fetch_bytes,
            http.fetch_bytes_meta,
            lambda u: http.not_modified(u, etag='"e"', last_modified=None),
            lambda u: http.post_form(u, {"a": "b"}),
        ):
            call(url)
        assert [request.full_url for request, _t in seam.calls] == [sent] * 6

    def test_an_ascii_url_is_sent_unchanged(self, seam):
        url = "https://x.example/a%2Fb/c.png?w=1&h=%3D2;x=[1]#frag"
        http.fetch_bytes(url)
        assert seam.calls[0][0].full_url == url

    def test_not_modified_answers_false_for_a_host_idna_cannot_encode(self, seam):
        assert (
            http.not_modified(
                "https://" + "é" * 64 + ".example/a.png", etag='"e"', last_modified=None
            )
            is False
        )
        assert seam.calls == []

    def test_a_fetch_from_a_host_idna_cannot_encode_is_invalid_input(self, seam):
        with pytest.raises(InvalidInputError):
            http.fetch_bytes("https://" + "é" * 64 + ".example/a.png")

    @pytest.mark.parametrize(
        "url", ["https://й@x.example/a.png", "https://x.example:١٢/a.png", "https://ü@[::1]/a"]
    )
    def test_a_non_ascii_userinfo_or_port_is_invalid_input(self, seam, url):
        """urllib writes both into the Host header, which is Latin-1 at best."""
        with pytest.raises(InvalidInputError):
            http.fetch_bytes(url)
        assert seam.calls == []

    def test_a_url_with_no_readable_host_is_invalid_input(self, seam):
        url = "https://[ü/a.png"
        assert http.not_modified(url, etag='"e"', last_modified=None) is False
        with pytest.raises(InvalidInputError):
            http.fetch_bytes(url)
        assert seam.calls == []

    @pytest.mark.parametrize(
        "host",
        [
            "straße.de",
            "STRAẞE.de",
            "ς.example",
            "a\u200db.example",
            "a\u200cb.example",
            "a\u2024com",
            "bücher\u2488evil.com",
            "a\u10a0b.example",
            "a\u1c90b.example",
            "a\u3164b.example",
            "a\u180fb.example",
            "a\U0001bca0b.example",
        ],
    )
    def test_a_host_a_browser_would_send_elsewhere_is_refused(self, seam, host):
        """Python's IDNA 2003 encodes these unlike a browser, so the request would reach another host."""
        with pytest.raises(InvalidInputError):
            http.fetch_bytes(f"https://{host}/a.png")
        assert seam.calls == []
