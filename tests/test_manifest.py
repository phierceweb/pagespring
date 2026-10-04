"""manifest.json: the build, write, read contract and the hash ``ingest --if-changed`` compares."""

import hashlib

import pytest

from pagespring import __version__, manifest


def test_sha256_file_matches_stdlib(tmp_path):
    data = b"<h1>Fake</h1>\n"
    f = tmp_path / "doc.html"
    f.write_bytes(data)
    assert manifest.sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_sha256_file_matches_stdlib_across_chunk_boundaries(tmp_path):
    """The digest is streamed in 1 MiB chunks. A fixture smaller than one chunk
    never exercises the loop, so a broken chunk walk would still pass."""
    data = b"pagespring" * 300_000  # ~2.9 MB — spans three 1 MiB reads
    f = tmp_path / "big.pdf"
    f.write_bytes(data)
    assert manifest.sha256_file(f) == hashlib.sha256(data).hexdigest()


def _sample() -> manifest.Manifest:
    return manifest.build_manifest(
        source_url="https://docs.tableplus.com/",
        pattern="gitbook",
        slug="docs-tableplus-com",
        kind="markdown",
        deliverable="docs-tableplus-com.md",
        pages=62,
        size_bytes=123,
        sha256="deadbeef",
        images=0,
        ingested_at="2026-06-14T17:23:01Z",
    )


def test_build_manifest_carries_all_fields():
    m = _sample()
    assert m["schema_version"] == manifest.SCHEMA_VERSION
    assert m["pagespring_version"] == __version__
    assert m["source_url"] == "https://docs.tableplus.com/"
    assert m["pattern"] == "gitbook"
    assert m["slug"] == "docs-tableplus-com"
    assert m["kind"] == "markdown"
    assert m["deliverable"] == "docs-tableplus-com.md"
    assert m["pages"] == 62
    assert m["bytes"] == 123
    assert m["sha256"] == "deadbeef"
    assert m["images"] == 0
    assert m["ingested_at"] == "2026-06-14T17:23:01Z"


def test_manifest_carries_no_conversion_instructions():
    """pagespring records what a source IS, never how to convert it; a conversion
    hint staged here silently goes stale."""
    assert "convert_recipe" not in _sample()


def test_write_then_read_round_trips(tmp_path):
    m = _sample()
    path = manifest.write_manifest(tmp_path, m)
    assert path == tmp_path / manifest.MANIFEST_NAME
    assert path.exists()
    assert manifest.read_manifest(tmp_path) == m


def test_localized_sha256_defaults_to_none_and_round_trips(tmp_path):
    """None until an image pass runs, and kept through write and read: audit's integrity record for
    a localized file."""
    assert _sample()["localized_sha256"] is None

    m = _sample()
    m["localized_sha256"] = "b" * 64
    manifest.write_manifest(tmp_path, m)

    assert manifest.read_manifest(tmp_path)["localized_sha256"] == "b" * 64


def test_read_manifest_missing_returns_none(tmp_path):
    assert manifest.read_manifest(tmp_path) is None


def test_read_manifest_corrupt_returns_none(tmp_path):
    (tmp_path / manifest.MANIFEST_NAME).write_text("{not valid json", encoding="utf-8")
    assert manifest.read_manifest(tmp_path) is None


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"a string"', "null", "42", "true"])
def test_read_manifest_non_object_json_returns_none(tmp_path, payload):
    """Every caller indexes by key, so a non-object must read as no manifest, not raise TypeError."""
    (tmp_path / manifest.MANIFEST_NAME).write_text(payload, encoding="utf-8")
    assert manifest.read_manifest(tmp_path) is None


def test_read_manifest_invalid_utf8_returns_none(tmp_path):
    """A corrupt byte reads as "no record", exactly like corrupt JSON: a UnicodeDecodeError
    escaping from here kills a whole corpus sweep."""
    (tmp_path / manifest.MANIFEST_NAME).write_bytes(b'{"slug": "\xff caf\xe9"}')
    assert manifest.read_manifest(tmp_path) is None


def test_a_failed_write_leaves_the_previous_manifest_intact(tmp_path, monkeypatch):
    """A bare ``write_text`` truncates first; one interrupted run would leave the slug unrecorded."""
    import pf_core.utils.io as io_mod

    original = _sample()
    manifest.write_manifest(tmp_path, original)

    def boom(src, dst):
        raise OSError("killed mid-write")

    monkeypatch.setattr(io_mod.os, "replace", boom)

    updated = _sample()
    updated["sha256"] = "f" * 64
    with pytest.raises(OSError):
        manifest.write_manifest(tmp_path, updated)

    assert manifest.read_manifest(tmp_path) == original, "the old manifest was destroyed"
    strays = list(tmp_path.glob(f".{manifest.MANIFEST_NAME}.*"))
    assert not strays, f"temp file left behind: {strays}"


def test_the_architecture_doc_shows_the_current_manifest_schema():
    import json
    import re
    from pathlib import Path

    doc = (Path(__file__).parents[1] / "docs" / "architecture.md").read_text(encoding="utf-8")
    sample = json.loads(re.search(r"```json\n(\{\n  \"schema_version\".*?\n\})\n```", doc, re.S)[1])

    assert sample["schema_version"] == manifest.SCHEMA_VERSION
    assert set(sample) == set(manifest.Manifest.__annotations__) - {"image_pass_open"}
