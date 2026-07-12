"""The per-slug manifest.json — provenance record beside each deliverable.

Pure stdlib (hashlib/json/datetime); no network, no pattern machinery. These
pin the build → write → read contract and the hash that `ingest --if-changed`
compares against.
"""

import hashlib

from pagespring import __version__, manifest


def test_sha256_file_matches_stdlib(tmp_path):
    data = b"<h1>Fake</h1>\n"
    f = tmp_path / "doc.html"
    f.write_bytes(data)
    assert manifest.sha256_file(f) == hashlib.sha256(data).hexdigest()


def _sample() -> manifest.Manifest:
    return manifest.build_manifest(
        source_url="https://docs.tableplus.com/",
        pattern="gitbook",
        slug="docs-tableplus-com",
        kind="markdown",
        deliverable="docs-tableplus-com.md",
        convert_recipe=["--split-sections"],
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
    assert m["convert_recipe"] == ["--split-sections"]
    assert m["pages"] == 62
    assert m["bytes"] == 123
    assert m["sha256"] == "deadbeef"
    assert m["images"] == 0
    assert m["ingested_at"] == "2026-06-14T17:23:01Z"


def test_write_then_read_round_trips(tmp_path):
    m = _sample()
    path = manifest.write_manifest(tmp_path, m)
    assert path == tmp_path / manifest.MANIFEST_NAME
    assert path.exists()
    assert manifest.read_manifest(tmp_path) == m


def test_read_manifest_missing_returns_none(tmp_path):
    assert manifest.read_manifest(tmp_path) is None


def test_read_manifest_corrupt_returns_none(tmp_path):
    (tmp_path / manifest.MANIFEST_NAME).write_text("{not valid json", encoding="utf-8")
    assert manifest.read_manifest(tmp_path) is None
