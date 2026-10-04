"""Optional image localizer — download + re-point refs (mocked fetch)."""

import hashlib
import urllib.error
from email.message import Message

import pytest

from pagespring import _image_cache, http, images

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


def test_non_image_body_keeps_remote_ref(tmp_path, monkeypatch):
    """A 200 carrying a login interstitial instead of image bytes must fail the ref,
    not get saved under a guessed .png extension."""
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/login.png)\n![b](https://x.com/ok.png)\n", encoding="utf-8")

    def fetch(url, **kwargs):
        if url.endswith("login.png"):
            return url, b"<!DOCTYPE html><html><body>Sign in</body></html>", _meta()
        return url, _PNG, _meta()

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert images.download_images(doc, tmp_path / "images") == 1
    assert [p.name for p in (tmp_path / "images").glob("*")] == ["ok.png"]
    text = doc.read_text(encoding="utf-8")
    assert "](https://x.com/login.png)" in text
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
    """localize erases the remote URL, so the sidecar is the only record a refresh can reuse."""
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

    reused = _image_cache.reuse_unchanged(doc, tmp_path)

    assert reused == 1
    assert probed == ["https://x.com/a.png"]  # the unknown URL is not probed
    text = doc.read_text(encoding="utf-8")
    assert "](images/a.png)" in text
    assert "](https://x.com/new.png)" in text  # left for localize to fetch
    assert images.count_remote_images(doc) == 1


def test_reuse_unchanged_paces_every_probe_it_sends(tmp_path, monkeypatch):
    """A probe carrying validators is a request to the image host, paced like a
    download; a record with none skips the probe for a full fetch, paced the same."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    records = []
    for name, etag in (("a.png", '"aaa"'), ("b.png", '"bbb"'), ("c.png", None)):
        (imgs / name).write_bytes(_PNG)
        records.append(
            {
                "local": name,
                "source_url": f"https://x.com/{name}",
                "etag": etag,
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            }
        )
    images.write_sidecar(tmp_path, records)
    doc = tmp_path / "d.md"
    doc.write_text(
        "".join(f"![{r['local']}]({r['source_url']})\n" for r in records), encoding="utf-8"
    )
    events = []

    def not_modified(url, *, etag, last_modified):
        events.append(("probe", url))
        return etag is not None

    def fetch(url, **kwargs):
        events.append(("fetch", url))
        return url, _PNG, _meta()

    monkeypatch.setattr(http, "not_modified", not_modified)
    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append(("sleep", None)))

    _image_cache.reuse_unchanged(doc, tmp_path)

    assert events == [
        ("probe", "https://x.com/a.png"),
        ("sleep", None),
        ("probe", "https://x.com/b.png"),
        ("sleep", None),
        ("fetch", "https://x.com/c.png"),
        ("sleep", None),
    ]


def test_reuse_unchanged_does_not_corrupt_a_prefix_sibling_ref(tmp_path, monkeypatch):
    """Sizing variants make one URL a prefix of another; an unanchored replace mangles the longer."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "pic.png").write_bytes(_PNG)
    (imgs / "pic-2.png").write_bytes(_JPG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "pic.png",
                "source_url": "https://cdn/pic.png",
                "etag": '"p1"',
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            },
            {
                "local": "pic-2.png",
                "source_url": "https://cdn/pic.png?wid=1200",
                "etag": '"p2"',
                "last_modified": None,
                "sha256": hashlib.sha256(_JPG).hexdigest(),
                "bytes": len(_JPG),
            },
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text(
        '<img src="https://cdn/pic.png">\n<img src="https://cdn/pic.png?wid=1200">\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(http, "not_modified", lambda u, **k: True)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 2

    text = doc.read_text(encoding="utf-8")
    assert 'src="images/pic.png"' in text
    assert 'src="images/pic-2.png"' in text
    assert "images/pic.png?wid=1200" not in text
    assert images.count_remote_images(doc) == 0


def test_reuse_unchanged_probes_the_decoded_url(tmp_path, monkeypatch):
    """The sidecar is keyed by the decoded URL that was actually fetched, so probing
    the document's `&amp;`-escaped form could never match the stored validators."""
    imgs = tmp_path / "images"
    imgs.mkdir()
    (imgs / "i.png").write_bytes(_PNG)
    images.write_sidecar(
        tmp_path,
        [
            {
                "local": "i.png",
                "source_url": "https://cdn/i.png?a=1&wid=9",
                "etag": '"i1"',
                "last_modified": None,
                "sha256": hashlib.sha256(_PNG).hexdigest(),
                "bytes": len(_PNG),
            }
        ],
    )
    doc = tmp_path / "d.md"
    doc.write_text('<img src="https://cdn/i.png?a=1&amp;wid=9">\n', encoding="utf-8")

    probed = []

    def not_modified(url, *, etag, last_modified):
        probed.append(url)
        return url == "https://cdn/i.png?a=1&wid=9"

    monkeypatch.setattr(http, "not_modified", not_modified)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 1
    assert probed == ["https://cdn/i.png?a=1&wid=9"]
    assert 'src="images/i.png"' in doc.read_text(encoding="utf-8")
    assert images.count_remote_images(doc) == 0


def _cached(tmp_path, local, url, data, *, etag='"old"'):
    """A localized image on disk, its sidecar record, and a doc whose ref is remote again."""
    imgs = tmp_path / "images"
    imgs.mkdir(exist_ok=True)
    (imgs / local).write_bytes(data)
    record = {
        "local": local,
        "source_url": url,
        "etag": etag,
        "last_modified": None,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    images.write_sidecar(tmp_path, [record])
    doc = tmp_path / "d.md"
    doc.write_text(f"![i]({url})\n", encoding="utf-8")
    return doc, record


def _unreachable(url, **kwargs):
    raise urllib.error.URLError("temporary failure in name resolution")


def test_reuse_unchanged_refreshes_a_changed_image_in_place(tmp_path, monkeypatch):
    """New bytes at an unchanged URL take the cached file's name and record."""
    doc, _rec = _cached(tmp_path, "banner.png", "https://x.com/banner.png", _PNG)
    fresh = _PNG + b"-v2"
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, fresh, _meta(etag='"new"')))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 1

    assert (tmp_path / "images" / "banner.png").read_bytes() == fresh
    assert "](images/banner.png)" in doc.read_text(encoding="utf-8")
    [rec] = images.read_sidecar(tmp_path)
    assert rec["etag"] == '"new"'
    assert rec["sha256"] == hashlib.sha256(fresh).hexdigest()
    assert rec["bytes"] == len(fresh)


@pytest.mark.parametrize("etag", ['"old"', None], ids=["probe-failed", "no-validators"])
def test_reuse_unchanged_keeps_the_cached_image_when_its_source_is_unreachable(
    tmp_path, monkeypatch, etag
):
    """The probe answers False for a network error too, and a tokened URL may not come back."""
    doc, rec = _cached(tmp_path, "fig.png", "https://x.com/fig.png?token=t", _PNG, etag=etag)
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(http, "fetch_bytes_meta", _unreachable)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 0

    assert (tmp_path / "images" / "fig.png").read_bytes() == _PNG
    assert images.read_sidecar(tmp_path) == [rec]
    assert images.count_remote_images(doc) == 1  # still pending, so prune stays off


def test_reuse_unchanged_keeps_the_cached_image_when_a_page_comes_back(tmp_path, monkeypatch):
    """An expired token often answers 200 with a login page rather than an error."""
    doc, rec = _cached(tmp_path, "fig.png", "https://x.com/fig.png", _PNG)
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(
        http, "fetch_bytes_meta", lambda u, **k: (u, b"<html>sign in</html>", _meta())
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 0

    assert (tmp_path / "images" / "fig.png").read_bytes() == _PNG
    assert images.read_sidecar(tmp_path) == [rec]


def test_reuse_unchanged_reuses_identical_bytes_from_a_server_without_validators(
    tmp_path, monkeypatch
):
    doc, _rec = _cached(tmp_path, "a.png", "https://x.com/a.png", _PNG, etag=None)
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"e1"')))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 1

    assert "](images/a.png)" in doc.read_text(encoding="utf-8")
    assert [r["etag"] for r in images.read_sidecar(tmp_path)] == ['"e1"']


def test_reuse_unchanged_leaves_a_format_change_to_the_localizer(tmp_path, monkeypatch):
    """An extensionless URL's name carries the sniffed type, so new bytes of another
    type belong under another name — the old file stays until they land."""
    doc, rec = _cached(tmp_path, "hero.png", "https://cdn.x.com/assets/hero", _PNG)
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _JPG, _meta()))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 0

    assert (tmp_path / "images" / "hero.png").read_bytes() == _PNG
    assert images.read_sidecar(tmp_path) == [rec]
    assert images.count_remote_images(doc) == 1


def test_reuse_unchanged_is_a_noop_without_a_sidecar(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/a.png)\n", encoding="utf-8")
    assert _image_cache.reuse_unchanged(doc, tmp_path) == 0


def test_remote_image_urls_agrees_with_count_remote_images(tmp_path):
    """remote_image_urls uses a private pf-core matcher; pin it to the counter, not copied regexes."""
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
    """The localizer claims names against the disk, so a download beside the stale file would add a
    suffix on every refresh."""
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

    _image_cache.reuse_unchanged(doc, tmp_path)
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

    pruned = _image_cache.prune_orphans(doc, tmp_path)

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

    assert _image_cache.prune_orphans(doc, tmp_path) == 0
    assert (imgs / "a.png").exists()


def test_two_urls_with_identical_bytes_get_separate_records(tmp_path, monkeypatch):
    """Joining by hash alone collapses same-byte files into one record, leaving the other untracked."""
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


def test_entity_escaped_url_is_decoded_before_fetching(monkeypatch):
    """The localizer hands back the attribute verbatim, ``&amp;`` included, and a CDN serves its
    default rendition for ``amp;wid``."""
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        return url, b"\x89PNG\r\n\x1a\n", {"etag": None, "last_modified": None}

    monkeypatch.setattr(http, "fetch_bytes_meta", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    fetcher = images._PacedFetcher()
    fetcher.get_bytes("https://s7.test/is/image/X/y?$png$&amp;jpegSize=200&amp;wid=1199")

    assert seen == ["https://s7.test/is/image/X/y?$png$&jpegSize=200&wid=1199"]
    assert "&amp;" not in fetcher.fetched[0][1]["source_url"]


def test_local_names_are_case_stable():
    """On a case-insensitive disk two names differing in case are one file, and a suffixed copy
    strands the other's ref."""
    assert images.local_name("https://x/Hero-Banner?$pjpeg$&wid=1920") == images.local_name(
        "https://x/hero-banner?$pjpeg$&wid=1920"
    )
    assert images.local_name("https://x/Panel.PNG") == "panel.png"


def test_existing_mixed_case_files_are_normalised_before_localize(tmp_path):
    """A mixed-case cached file is renamed lowercase along with its refs and
    record, before a download can land on it (see ``normalize_case``)."""
    slug_dir = tmp_path / "s"
    (slug_dir / "images").mkdir(parents=True)
    doc = slug_dir / "s.md"
    doc.write_text("![a](images/Panel_0001.jpg)\n![b](images/keep.png)\n", encoding="utf-8")
    (slug_dir / "images" / "Panel_0001.jpg").write_bytes(b"jpg")
    (slug_dir / "images" / "keep.png").write_bytes(b"png")
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "Panel_0001.jpg",
                "source_url": "https://x/Panel_0001.jpg",
                "etag": None,
                "last_modified": None,
                "sha256": "d" * 64,
                "bytes": 3,
            }
        ],
    )

    _image_cache.normalize_case(doc, slug_dir)

    assert (slug_dir / "images" / "panel_0001.jpg").is_file()
    assert "images/panel_0001.jpg" in doc.read_text(encoding="utf-8")
    assert "images/Panel_0001.jpg" not in doc.read_text(encoding="utf-8")
    assert images.read_sidecar(slug_dir)[0]["local"] == "panel_0001.jpg"
    assert (slug_dir / "images" / "keep.png").is_file()  # untouched


def test_provenance_does_not_claim_an_earlier_runs_identical_file(tmp_path, monkeypatch):
    """A logo reused elsewhere hashes like the earlier copy; claiming that file strands the new one."""
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    imgs = tmp_path / "images"
    doc = tmp_path / "d.md"

    # Run 1: x.com/logo.png -> images/logo.png
    doc.write_text("![a](https://x.com/logo.png)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"x"')))
    images.download_images(doc, imgs)
    assert (imgs / "logo.png").is_file()

    # Run 2: a different host serves the SAME bytes under a different name.
    doc.write_text("![b](https://y.com/zebra.png)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"y"')))
    images.download_images(doc, imgs)

    by_url = {r["source_url"]: r for r in images.read_sidecar(tmp_path)}
    assert by_url["https://x.com/logo.png"]["local"] == "logo.png", "earlier record rewritten"
    assert "https://y.com/zebra.png" in by_url, "this run's download went unrecorded"
    assert by_url["https://y.com/zebra.png"]["local"] != "logo.png", (
        "record attached to the earlier run's file"
    )
    assert (imgs / by_url["https://y.com/zebra.png"]["local"]).is_file()


def test_provenance_guard_survives_a_corrupt_sidecar(tmp_path, monkeypatch):
    """Prior files are read from the directory: ``read_sidecar`` answers [] for a corrupt sidecar,
    exactly when the guard matters."""
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    imgs = tmp_path / "images"
    doc = tmp_path / "d.md"

    doc.write_text("![a](https://x.com/logo.png)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"x"')))
    images.download_images(doc, imgs)
    (tmp_path / images.SIDECAR_NAME).write_text('{"images": [', encoding="utf-8")

    doc.write_text("![b](https://y.com/zebra.png)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"y"')))
    images.download_images(doc, imgs)

    by_url = {r["source_url"]: r for r in images.read_sidecar(tmp_path)}
    rec = by_url.get("https://y.com/zebra.png")
    assert rec is not None, "this run's download went unrecorded"
    assert rec["local"] == "zebra.png", "record attached to the earlier run's file"
    assert (imgs / "zebra.png").is_file()


def test_provenance_leaves_an_ambiguous_hash_match_unrecorded(tmp_path, monkeypatch):
    """Two extensionless same-byte downloads leave nothing to break the tie; wrong provenance is
    worse than none."""
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    imgs = tmp_path / "images"
    doc = tmp_path / "d.md"
    doc.write_text("![a](https://x.com/img/one)\n![b](https://x.com/img/two)\n", encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta(etag='"e"')))

    images.download_images(doc, imgs)

    staged = sorted(p.name for p in imgs.glob("*"))
    assert len(staged) == 2, f"both downloads must land: {staged}"
    recorded = {r["source_url"] for r in images.read_sidecar(tmp_path)}
    assert recorded == set(), f"an ambiguous match was attributed anyway: {recorded}"


def test_a_killed_reuse_pass_leaves_the_deliverable_intact(tmp_path, monkeypatch):
    """A bare ``write_text`` truncates first, so a kill mid-rewrite would leave a partial manual."""
    import pf_core.utils.io as io_mod

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
    original = "![a](https://x.com/a.png)\n"
    doc.write_text(original, encoding="utf-8")

    monkeypatch.setattr(http, "not_modified", lambda url, **kw: True)

    def boom(src, dst):
        raise OSError("killed mid-write")

    monkeypatch.setattr(io_mod.os, "replace", boom)

    with pytest.raises(OSError):
        _image_cache.reuse_unchanged(doc, tmp_path)

    assert doc.read_text(encoding="utf-8") == original, "the deliverable was destroyed"
    assert not list(tmp_path.glob(".d.md.*")), "temp file left behind"


def test_an_interrupted_localize_records_every_image_that_landed(tmp_path, monkeypatch):
    """The refs of landed images are already local, so the sidecar is their only
    record of where they came from."""
    doc = tmp_path / "d.md"
    doc.write_text(
        "".join(f"![{n}](https://x.com/{n}.png)\n" for n in range(1, 8)), encoding="utf-8"
    )

    def fetch(url, **kwargs):
        if url.endswith("/7.png"):
            raise KeyboardInterrupt
        return url, _PNG + url.encode(), _meta(etag=f'"{url[-5]}"')

    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(KeyboardInterrupt):
        images.download_images(doc, tmp_path / "images", checkpoint_every=1)

    landed = sorted(p.name for p in (tmp_path / "images").glob("*"))
    assert landed == [f"{n}.png" for n in range(1, 7)]
    recs = {r["local"]: r for r in images.read_sidecar(tmp_path)}
    assert sorted(recs) == landed
    assert recs["3.png"]["source_url"] == "https://x.com/3.png"
    assert recs["3.png"]["etag"] == '"3"'


def test_reuse_unchanged_reuses_an_extensionless_url_with_a_dot_in_its_name(tmp_path, monkeypatch):
    """``asset-v3.1-large`` has no image extension, so the localizer named it by the
    sniffed type; a suffix check reading ``.1-large`` re-downloads it every pass."""
    url = "https://cdn.example.com/assets/asset-v3.1-large"
    doc, _rec = _cached(tmp_path, "asset-v3.1-large.png", url, _PNG, etag=None)
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, _meta()))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 1
    assert "](images/asset-v3.1-large.png)" in doc.read_text(encoding="utf-8")
