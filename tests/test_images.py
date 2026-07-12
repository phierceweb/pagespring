"""Optional image localizer — download + re-point refs (mocked fetch)."""

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
        return url, (_PNG if url.endswith("a.png") else _JPG)

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
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
    monkeypatch.setattr(http, "fetch_bytes", lambda u, **k: (u, _PNG))
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
        return url, _JPG

    monkeypatch.setattr(http, "fetch_bytes", fetch)
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
        return url, _PNG

    monkeypatch.setattr(http, "fetch_bytes", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    images.download_images(doc, tmp_path / "images", checkpoint_every=1)

    assert "](images/1.png)" in doc_states[1]  # 1st image checkpointed before 2nd fetch


def test_count_remote_images_ignores_localized(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(
        '![a](https://x/1.png)\n![b](images/2.png)\n<img src="https://x/3.png">\n',
        encoding="utf-8",
    )
    assert images.count_remote_images(doc) == 2  # local images/2.png not counted
