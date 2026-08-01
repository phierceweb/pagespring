"""Optional image localizer — download + re-point refs (mocked fetch)."""

import hashlib
import urllib.error
from email.message import Message

from pagespring import http, images

_PNG = b"\x89PNG\r\n\x1a\n" + b"pngbody"
_JPG = b"\xff\xd8\xff" + b"jpgbody"


def test_downloads_md_and_html_refs_dedups(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text(
        "# Doc\n\n"
        "![a](https://x.com/a.png)\n\n"
        '<img src="https://x.com/pics/b.jpg" alt="b">\n\n'
        "![again](https://x.com/a.png)\n",
        encoding="utf-8",
    )

    def fake_fetch_bytes(url, **kwargs):
        return url, (_PNG if url.endswith("a.png") else _JPG), _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", fake_fetch_bytes)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    n = images.download_images(doc, tmp_path / "images")

    assert n == 2  # the duplicate URL is fetched once
    assert sorted(p.name for p in (tmp_path / "images").glob("*")) == ["a.png", "b.jpg"]
    text = doc.read_text(encoding="utf-8")
    assert "](images/a.png)" in text
    assert 'src="images/b.jpg"' in text
    assert "https://x.com" not in text  # every remote ref rewritten


def test_no_images_is_noop(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("# nothing to download here\n", encoding="utf-8")
    assert images.download_images(doc, tmp_path / "images") == 0
    assert not (tmp_path / "images").exists()


def test_extensionless_url_sniffed(tmp_path, monkeypatch):
    doc = tmp_path / "d.md"
    doc.write_text("![x](https://cdn.example/assets/abcd1234)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta()))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    n = images.download_images(doc, tmp_path / "images")

    assert n == 1
    saved = [p.name for p in (tmp_path / "images").glob("*")]
    assert saved == ["abcd1234.png"]  # extension sniffed from magic bytes
    assert "](images/abcd1234.png)" in doc.read_text(encoding="utf-8")


def test_resume_seeds_used_names_so_prior_run_not_clobbered(tmp_path, monkeypatch):
    """On a re-run, an already-local ref is left alone, and a NEW image whose name
    would collide with a prior run's file gets a suffix instead of overwriting it."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "old.png").write_bytes(_PNG)  # from a prior run
    doc = tmp_path / "d.md"
    doc.write_text("![a](images/old.png)\n![b](https://other.com/old.png)\n", encoding="utf-8")
    fetched = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, _JPG, _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    n = images.download_images(doc, images_dir)

    assert fetched == ["https://other.com/old.png"]  # the already-local ref not re-fetched
    assert n == 1
    assert (images_dir / "old.png").read_bytes() == _PNG  # prior file untouched
    assert (images_dir / "old-2.png").read_bytes() == _JPG  # new one suffixed
    text = doc.read_text(encoding="utf-8")
    assert "](images/old.png)" in text and "](images/old-2.png)" in text
    assert "https://other.com" not in text


def test_checkpoints_progress_during_run(tmp_path, monkeypatch):
    """Progress is written to the deliverable as it goes (so a killed big-book run
    keeps what it localized): by the 2nd fetch, the 1st image is already in the doc."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/1.png)\n![b](https://x.com/2.png)\n", encoding="utf-8")
    doc_states = []

    def fetch(url, **kwargs):
        doc_states.append(doc.read_text(encoding="utf-8"))  # doc state at each fetch
        return url, _PNG, _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    images.download_images(doc, tmp_path / "images", checkpoint_every=1)

    assert "](images/1.png)" in doc_states[1]  # 1st image checkpointed before 2nd fetch


def test_paces_between_images(tmp_path, monkeypatch):
    """One polite delay per download — a localize of a big book is still a crawl."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/1.png)\n![b](https://x.com/2.png)\n", encoding="utf-8")
    paced: list[float] = []
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta()))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: paced.append(0.25))

    images.download_images(doc, tmp_path / "images")

    assert len(paced) == 2


def test_failed_download_keeps_remote_ref(tmp_path, monkeypatch):
    """An unfetchable image keeps its remote ref, so the doc still renders and the
    remaining-count tells the caller to re-run."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/gone.png)\n![b](https://x.com/ok.png)\n", encoding="utf-8")

    def fetch(url, **kwargs):
        if url.endswith("gone.png"):
            raise urllib.error.HTTPError(url, 404, "gone", Message(), None)
        return url, _PNG, _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert images.download_images(doc, tmp_path / "images") == 1
    text = doc.read_text(encoding="utf-8")
    assert "](https://x.com/gone.png)" in text
    assert "](images/ok.png)" in text
    assert images.count_remote_images(doc) == 1


def test_count_remote_images_ignores_localized(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(
        '![a](https://x/1.png)\n![b](images/2.png)\n<img src="https://x/3.png">\n',
        encoding="utf-8",
    )
    assert images.count_remote_images(doc) == 2  # local images/2.png not counted


# --- image sidecar: per-image provenance so a refresh can skip unchanged images ---


def _meta(etag=None, last_modified=None):
    return {"etag": etag, "last_modified": last_modified}


def test_localize_writes_a_sidecar_with_per_image_provenance(tmp_path, monkeypatch):
    """localize erases the remote URL from the deliverable, so without a sidecar
    there is no record of where an image came from and a refresh must re-download
    everything."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/a.png)\n![b](https://x.com/b.jpg)\n", encoding="utf-8")

    def fetch(url, **kwargs):
        body = _PNG if url.endswith("a.png") else _JPG
        return (
            url,
            body,
            _meta(
                etag='"aaa"' if url.endswith("a.png") else None,
                last_modified="Wed, 01 Jul 2026 00:00:00 GMT",
            ),
        )

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    images.download_images(doc, tmp_path / "images")
    recs = images.read_sidecar(tmp_path)

    by_url = {r["source_url"]: r for r in recs}
    assert set(by_url) == {"https://x.com/a.png", "https://x.com/b.jpg"}
    a = by_url["https://x.com/a.png"]
    assert a["local"] == "a.png"
    assert a["etag"] == '"aaa"'
    assert a["last_modified"] == "Wed, 01 Jul 2026 00:00:00 GMT"
    assert a["sha256"] == hashlib.sha256(_PNG).hexdigest()
    assert a["bytes"] == len(_PNG)


def test_sidecar_merges_across_resumed_runs(tmp_path, monkeypatch):
    """localize is resumable; a second pass must not drop the first pass's records."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/a.png)\n![b](https://x.com/b.jpg)\n", encoding="utf-8")
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    def only_a(url, **kwargs):
        if url.endswith("b.jpg"):
            raise urllib.error.HTTPError(url, 500, "boom", Message(), None)
        return url, _PNG, _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", only_a)
    images.download_images(doc, tmp_path / "images")
    assert [r["source_url"] for r in images.read_sidecar(tmp_path)] == ["https://x.com/a.png"]

    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _JPG, _meta()))
    images.download_images(doc, tmp_path / "images")

    assert {r["source_url"] for r in images.read_sidecar(tmp_path)} == {
        "https://x.com/a.png",
        "https://x.com/b.jpg",
    }


def test_reuse_unchanged_rewrites_refs_without_downloading(tmp_path, monkeypatch):
    """The payoff: on a refreshed deliverable, an image whose URL is in the sidecar
    and whose server answers 304 is re-pointed at the local file — no download."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "a.png").write_bytes(_PNG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "a.png",
                "source_url": "https://x.com/a.png",
                "etag": '"aaa"',
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            }
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/a.png)\n![n](https://x.com/new.png)\n", encoding="utf-8")

    probed = []

    def not_modified(url, *, etag, last_modified):
        probed.append(url)
        return url == "https://x.com/a.png"

    monkeypatch.setattr(http, "not_modified", not_modified)

    reused = images.reuse_unchanged(doc, tmp_path)

    assert reused == 1
    assert probed == ["https://x.com/a.png"]  # the unknown URL is not probed
    text = doc.read_text(encoding="utf-8")
    assert "](images/a.png)" in text
    assert "](https://x.com/new.png)" in text  # left for localize to fetch
    assert images.count_remote_images(doc) == 1


def test_reuse_unchanged_refetches_when_server_says_changed(tmp_path, monkeypatch):
    """A stable-name image (izotope's nectar-banner.png) keeps its URL when the
    bytes change — a 200 instead of 304 must leave the ref remote."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "banner.png").write_bytes(_PNG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "banner.png",
                "source_url": "https://x.com/banner.png",
                "etag": '"old"',
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            }
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text("![b](https://x.com/banner.png)\n", encoding="utf-8")
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)

    assert images.reuse_unchanged(doc, tmp_path) == 0
    assert images.count_remote_images(doc) == 1


def test_reuse_unchanged_is_a_noop_without_a_sidecar(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/a.png)\n", encoding="utf-8")
    assert images.reuse_unchanged(doc, tmp_path) == 0


def test_remote_image_urls_agrees_with_count_remote_images(tmp_path):
    """remote_image_urls delegates to a private pf-core matcher. If pf-core changes
    it, the rewrite would silently target a different set than the counter reports
    — so pin them together here rather than duplicating the regexes."""
    doc = tmp_path / "d.md"
    doc.write_text(
        "![a](https://x/1.png)\n"
        "![dup](https://x/1.png)\n"
        '<img src="https://x/2.jpg">\n'
        "![local](images/3.png)\n"
        "![notimage](https://x/page.html)\n"
        "![ext-less](https://cdn.example/assets/abcd1234)\n",
        encoding="utf-8",
    )
    urls = images.remote_image_urls(doc)
    assert len(urls) == images.count_remote_images(doc)
    assert "https://x/1.png" in urls
    assert urls.count("https://x/1.png") == 1  # deduped
    assert "images/3.png" not in urls


def test_changed_image_replaces_in_place_instead_of_suffixing(tmp_path, monkeypatch):
    """A stable-name vendor (izotope's nectar-banner.png) ships new bytes at the SAME
    URL. The localizer claims names against what is on disk, so leaving the stale file
    there would write nectar-banner-2.png and orphan the original — and after N
    refreshes you have -2, -3, -4. Free the name so the fresh download takes it."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "banner.png").write_bytes(_PNG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "banner.png",
                "source_url": "https://x.com/banner.png",
                "etag": '"v1"',
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            }
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text("![b](https://x.com/banner.png)\n", encoding="utf-8")

    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)  # server: changed
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _JPG, _meta(etag='"v2"')))

    images.reuse_unchanged(doc, tmp_path)
    images.download_images(doc, imgs)

    assert sorted(p.name for p in imgs.glob("*")) == ["banner.png"]  # no banner-2
    assert (imgs / "banner.png").read_bytes() == _JPG  # replaced in place
    assert "](images/banner.png)" in doc.read_text(encoding="utf-8")
    rec = images.read_sidecar(tmp_path)[0]
    assert rec["etag"] == '"v2"'


def test_prune_orphans_deletes_unreferenced_files_and_records(tmp_path):
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "used.png").write_bytes(_PNG)
    (imgs / "gone.png").write_bytes(_JPG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "used.png",
                "source_url": "https://x/u.png",
                "etag": None,
                "last_modified": None,
                "sha256": "a",
                "bytes": 1,
            },
            {
                "local": "gone.png",
                "source_url": "https://x/g.png",
                "etag": None,
                "last_modified": None,
                "sha256": "b",
                "bytes": 1,
            },
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text("![u](images/used.png)\n", encoding="utf-8")

    pruned = images.prune_orphans(doc, tmp_path)

    assert pruned == 1
    assert sorted(p.name for p in imgs.glob("*")) == ["used.png"]
    assert [r["local"] for r in images.read_sidecar(tmp_path)] == ["used.png"]


def test_prune_orphans_refuses_while_remote_refs_remain(tmp_path):
    """Mid-localize the refs are still remote, so every local file looks unreferenced.
    Pruning then would delete the whole image cache."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "a.png").write_bytes(_PNG)
    doc = tmp_path / "d.md"
    doc.write_text("![a](images/a.png)\n![b](https://x.com/b.png)\n", encoding="utf-8")

    assert images.prune_orphans(doc, tmp_path) == 0
    assert (imgs / "a.png").exists()


def test_two_urls_with_identical_bytes_get_separate_records(tmp_path, monkeypatch):
    """A repeated logo served from two URLs yields two files with the SAME bytes.
    Joining downloads to files by content hash alone collapses them into one
    record, so the other file becomes untracked — and on the next refresh its
    name is still claimed, so the re-download suffixes instead of replacing."""
    doc = tmp_path / "d.md"
    doc.write_text(
        "![a](https://x.com/logo.png)\n![b](https://y.com/banner.png)\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag=f'"{u[-9:]}"'))
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    images.download_images(doc, tmp_path / "images")

    recs = {r["source_url"]: r["local"] for r in images.read_sidecar(tmp_path)}
    assert recs == {
        "https://x.com/logo.png": "logo.png",
        "https://y.com/banner.png": "banner.png",
    }
