"""_aura — the guest Aura context from a page shell, one-action POSTs, records (mocked http)."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from pf_core.exceptions import ClientError, InvalidInputError

from pagespring import http
from pagespring.patterns import _aura

_CTX = {
    "mode": "PROD",
    "app": "siteforce:communityApp",
    "fwuid": "FWUID123",
    "loaded": {"APPLICATION@markup://siteforce:communityApp": "1881_abc"},
    "pathPrefix": "",
}


def shell(prefix: str = "", ctx: dict | None = None) -> str:
    """A community page shell: the bootstrap script URL carries the Aura context."""
    encoded = quote(json.dumps(ctx or _CTX, separators=(",", ":")), safe="")
    return (
        "<html><head><title>Support</title>"
        f'<script src="{prefix}/s/sfsites/l/{encoded}/bootstrap.js?aura.attributes=x"></script>'
        "</head><body></body></html>"
    )


class TestSiteFromShell:
    def test_endpoint_sits_beside_the_bootstrap_script(self):
        site = _aura.site_from_shell("https://help.acme.example/s/article/A", shell())
        assert site.endpoint == "https://help.acme.example/s/sfsites/aura"

    def test_a_path_prefix_is_kept(self):
        site = _aura.site_from_shell("https://acme.example/support/s/topic/0TO1", shell("/support"))
        assert site.endpoint == "https://acme.example/support/s/sfsites/aura"

    def test_context_carries_the_framework_and_app_versions(self):
        ctx = json.loads(_aura.site_from_shell("https://help.acme.example/s/", shell()).context)
        assert ctx["fwuid"] == "FWUID123"
        assert ctx["app"] == "siteforce:communityApp"
        assert ctx["loaded"] == _CTX["loaded"]

    def test_a_page_without_the_bootstrap_is_refused(self):
        with pytest.raises(InvalidInputError, match="Aura"):
            _aura.site_from_shell("https://help.acme.example/s/article/A", "<html>plain</html>")


SITE = _aura.AuraSite(endpoint="https://help.acme.example/s/sfsites/aura", context='{"fwuid":"F"}')


def _ok(return_value=None, records=None) -> str:
    providers = [{"type": "$Record", "values": {"records": records or {}}}]
    return json.dumps(
        {
            "actions": [{"id": "1;a", "state": "SUCCESS", "returnValue": return_value}],
            "context": {"globalValueProviders": providers},
        }
    )


class TestCall:
    def test_posts_one_action_as_a_guest(self, monkeypatch):
        sent = {}

        def fake_post(url, fields, **kw):
            sent.update(url=url, fields=fields)
            return url, _ok("ka01")

        monkeypatch.setattr(http, "post_form", fake_post)
        resp = _aura.call(SITE, "serviceComponent://X/ACTION$y", {"a": 1}, page_uri="/s/article/A")

        assert sent["url"] == SITE.endpoint
        action = json.loads(sent["fields"]["message"])["actions"][0]
        assert action["descriptor"] == "serviceComponent://X/ACTION$y"
        assert action["params"] == {"a": 1}
        assert sent["fields"]["aura.context"] == SITE.context
        assert sent["fields"]["aura.token"] == "null"
        assert sent["fields"]["aura.pageURI"] == "/s/article/A"
        assert _aura.return_value(resp) == "ka01"

    def test_a_failed_action_raises(self, monkeypatch):
        body = json.dumps({"actions": [{"state": "ERROR", "error": [{"message": "nope"}]}]})
        monkeypatch.setattr(http, "post_form", lambda url, fields, **kw: (url, body))
        with pytest.raises(ClientError, match="nope"):
            _aura.call(SITE, "d", {}, page_uri="/s/")

    def test_a_framework_error_page_raises(self, monkeypatch):
        """Aura answers a refused request with a non-JSON ``*/{...}/*ERROR*/`` envelope."""
        body = '*/{"message":"Invalid request"}/*ERROR*/'
        monkeypatch.setattr(http, "post_form", lambda url, fields, **kw: (url, body))
        with pytest.raises(ClientError, match="Invalid request"):
            _aura.call(SITE, "d", {}, page_uri="/s/")


class TestRecordFields:
    def test_values_by_field_name_whatever_the_object(self):
        records = {
            "0TO1": {
                "Topic": {"record": {"fields": {"Name": {"value": "Player", "displayValue": None}}}}
            }
        }
        assert _aura.record_fields(json.loads(_ok(records=records)), "0TO1") == {"Name": "Player"}

    def test_a_record_missing_from_the_response_is_empty(self):
        assert _aura.record_fields(json.loads(_ok()), "ka0missing") == {}
