"""Guest calls to a Salesforce Experience Cloud site's Aura endpoint: the context a page shell
bootstraps with, one action per POST, and the records a response carries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from pf_core.exceptions import ClientError, InvalidInputError

from pagespring import http

# The shell's bootstrap script URL embeds the whole client context as URL-encoded JSON.
_BOOTSTRAP_RE = re.compile(r"""(/[^"'\s]*?/sfsites)/l/([^/"'\s]+)/bootstrap\.js""")
_ERROR_RE = re.compile(r'"message"\s*:\s*"([^"]+)"')


@dataclass(frozen=True)
class AuraSite:
    endpoint: str
    context: str  # the JSON aura.context every action is sent with


def site_from_shell(page_url: str, html: str) -> AuraSite:
    """The Aura endpoint and client context a community page shell bootstraps with.

    Raises:
        InvalidInputError: the page carries no Aura bootstrap, so it is not an Experience Cloud site.
    """
    m = _BOOTSTRAP_RE.search(html)
    if not m:
        raise InvalidInputError(
            f"{page_url} carries no Aura bootstrap script — not a Salesforce Experience Cloud page."
        )
    boot = json.loads(unquote(m.group(2)))
    context = {
        "mode": boot.get("mode", "PROD"),
        "fwuid": boot.get("fwuid"),
        "app": boot.get("app"),
        "loaded": boot.get("loaded", {}),
        "dn": [],
        "globals": {},
        "uad": True,
    }
    p = urlparse(page_url)
    return AuraSite(
        endpoint=f"{p.scheme}://{p.netloc}{m.group(1)}/aura", context=json.dumps(context)
    )


def call(
    site: AuraSite, descriptor: str, params: dict[str, Any], *, page_uri: str
) -> dict[str, Any]:
    """POST one action as the guest user; the whole response, since records ride in its context.

    Raises:
        ClientError: the framework refused the request or the action did not succeed.
    """
    message = {
        "actions": [
            {
                "id": "1;a",
                "descriptor": descriptor,
                "callingDescriptor": "UNKNOWN",
                "params": params,
            }
        ]
    }
    fields = {
        "message": json.dumps(message),
        "aura.context": site.context,
        "aura.pageURI": page_uri,
        "aura.token": "null",
    }
    _f, body = http.post_form(site.endpoint, fields)
    try:
        resp: dict[str, Any] = json.loads(body)
    except ValueError:
        m = _ERROR_RE.search(body)
        raise ClientError(
            f"Aura refused the request: {m.group(1) if m else body[:120]!r}",
            context={"endpoint": site.endpoint, "descriptor": descriptor},
        ) from None
    action = (resp.get("actions") or [{}])[0]
    if action.get("state") != "SUCCESS":
        errors = action.get("error") or [{}]
        raise ClientError(
            f"Aura action failed: {errors[0].get('message', action.get('state'))}",
            context={"endpoint": site.endpoint, "descriptor": descriptor},
        )
    return resp


def return_value(resp: dict[str, Any]) -> Any:
    return resp["actions"][0].get("returnValue")


def record_fields(resp: dict[str, Any], record_id: str) -> dict[str, Any]:
    """``{field: value}`` for the record the response's ``$Record`` provider holds under that id."""
    for provider in resp.get("context", {}).get("globalValueProviders", []):
        if provider.get("type") != "$Record":
            continue
        by_object = provider.get("values", {}).get("records", {}).get(record_id, {})
        for entry in by_object.values():
            fields = entry.get("record", {}).get("fields", {})
            return {name: f.get("value") for name, f in fields.items()}
    return {}
