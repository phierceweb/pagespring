"""_integrity — a killed image pass's progress told apart from damage."""

import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
from pw_foundation.exceptions import PreconditionError

from pagespring import (
    _image_cache,
    _integrity,
    audit,
    http,
    images,
    localize,
    manifest,
    orchestrate,
)
from pagespring.base import AcquireResult
from pagespring.config import cfg

_PNG = b"\x89PNG\r\n\x1a\nx"
_REMOTE = ("https://img.example/b.png", "https://img.example/c.png")
_BODY = '<h1>Bk</h1><img src="images/a.png">' + "".join(f'<img src="{u}">' for u in _REMOTE)


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


@pytest.fixture
def images_served(monkeypatch):
    monkeypatch.setattr(
        http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, {"etag": None, "last_modified": None})
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _localized_slug(tmp_path, body=_BODY):
    """A slug an earlier pass localized in part and recorded; remote refs remain."""
    slug_dir = tmp_path / "incoming" / "bk"
    (slug_dir / "images").mkdir(parents=True)
    (slug_dir / "images" / "a.png").write_bytes(_PNG)
    doc = slug_dir / "bk.html"
    doc.write_text(body, encoding="utf-8")
    manifest.write_manifest(
        slug_dir,
        manifest.build_manifest(
            source_url="https://x",
            pattern="fake",
            slug="bk",
            kind="html",
            deliverable="bk.html",
            pages=1,
            size_bytes=doc.stat().st_size,
            sha256="0" * 64,
            images=1,
            ingested_at="2026-09-22T00:00:00Z",
            localized_sha256=manifest.sha256_file(doc),
        ),
    )
    return slug_dir, doc


_MD_SAMPLE = '<h1>Bk</h1><pre><code class="language-markdown">![Logo](images/logo.svg)</code></pre>'


def test_an_html_deliverable_has_only_its_tags_as_local_refs():
    tags = '<img alt="a" src="images/a.png"><svg><image href="images/b.svg"/></svg>'

    md_syntax = "<h1>Bk</h1><p>![Logo](images/logo.svg)</p>"

    assert _integrity.local_image_refs(md_syntax, kind="html") == set()
    assert _integrity.local_image_refs(md_syntax, kind="markdown") == {"images/logo.svg"}
    assert _integrity.local_image_refs(tags, kind="html") == {"images/a.png", "images/b.svg"}
    assert _integrity.local_image_refs(tags, kind="markdown") == {"images/a.png", "images/b.svg"}


@pytest.mark.parametrize("kind", ["html", "markdown"])
def test_an_image_pass_may_have_rewritten_a_markdown_sample_in_any_kind(tmp_path, kind):
    """pw-core's localizer rewrites `](…)` wherever it appears, an html code sample included."""
    slug_dir, _doc = _localized_slug(tmp_path)
    m = manifest.read_manifest(slug_dir)
    m["images"], m["kind"] = 0, kind

    assert _integrity.image_pass_ran(slug_dir, m, _MD_SAMPLE) is True


def test_an_html_file_whose_only_local_ref_is_a_rewritten_sample_is_unverifiable(tmp_path):
    slug_dir, _doc = _localized_slug(tmp_path, body=_MD_SAMPLE)
    m = manifest.read_manifest(slug_dir)
    m["localized_sha256"], m["images"] = None, 0
    manifest.write_manifest(slug_dir, m)

    assert _state(slug_dir) == "unverifiable"


def test_a_tag_match_ends_at_its_own_closing_bracket():
    real_then_sample = (
        '<img src="https://img.example/a.png" alt="x"><code>&lt;img src="images/x.svg"&gt;'
    )
    text_then_sample = '<img src="https://img.example/a.png"> see &lt;img src="images/x.svg"&gt;'
    quoted_gt = '<img alt="a > b" src="images/a.png">'
    quoted_lt = '<img alt="a<b" src="images/a.png">'

    assert _integrity.local_image_refs(real_then_sample, kind="html") == set()
    assert _integrity.local_image_refs(text_then_sample, kind="html") == set()
    assert _integrity.local_image_refs(quoted_gt, kind="html") == {"images/a.png"}
    assert _integrity.local_image_refs(quoted_lt, kind="html") == {"images/a.png"}
    assert _integrity.local_image_refs('<img src="Images/A.png">', kind="html") == set()


@pytest.mark.parametrize(
    ("kind", "doc"),
    [
        ("markdown", "# T\n\n```md\n![a](images/x.png)\n```\n\n![b](images/kept.png)\n"),
        ("markdown", '# T\n\n~~~\n<img src="images/x.png">\n~~~\n\n![b](images/kept.png)\n'),
        ("markdown", "# T\n\n    ![a](images/x.png)\n\n![b](images/kept.png)\n"),
        ("markdown", "# T\n\nwrite `![a](images/x.png)` for ![b](images/kept.png)\n"),
        (
            "markdown",
            "# T\n\n<pre><code>![a](images/x.png)</code></pre>\n\n![b](images/kept.png)\n",
        ),
        ("markdown", "# T\n\n<p><code>![a](images/x.png)</code></p>\n\n![b](images/kept.png)\n"),
        (
            "markdown",
            '# T\n\n<PRE class="hl">\n![a](images/x.png)\n</PRE >\n\n![b](images/kept.png)\n',
        ),
    ],
    ids=["fence", "tilde-fence-tag", "indented", "span", "md-pre", "md-code", "md-PRE-attrs"],
)
def test_a_ref_in_a_code_sample_is_not_a_local_ref(kind, doc):
    """A sample that shows an image ref is text to the reader."""
    assert _integrity.local_image_refs(doc, kind=kind) == {"images/kept.png"}


@pytest.mark.parametrize("kind", ["html", "markdown"])
@pytest.mark.parametrize(
    "doc",
    [
        '<h1>T</h1><p><code><img src="images/k.png"> Save</code></p>',
        '<h1>T</h1><pre><img src="images/k.png"></pre>',
        '<h1>T</h1><pre><code>![a](images/x.png) <img src="images/k.png"></code></pre>',
    ],
    ids=["code", "pre", "beside-a-sample"],
)
def test_a_real_tag_inside_a_code_element_is_a_local_ref(kind, doc):
    """A browser renders an ``<img>`` wherever it sits; only markdown syntax there is text."""
    assert _integrity.local_image_refs(doc, kind=kind) == {"images/k.png"}


@pytest.mark.parametrize("tag", ["code-block", "code-group", "pre-wrap", "code.x", "prefix"])
def test_an_element_whose_name_only_begins_with_pre_or_code_is_not_code(tag):
    """Left open, a name read as ``pre`` or ``code`` would hide the image up to the later closer."""
    doc = f'# T\n\n<{tag} lang="x">a ![b](images/k.png) <code>x</code>\n'
    assert _integrity.local_image_refs(doc, kind="markdown") == {"images/k.png"}


@pytest.mark.parametrize(
    ("lead", "tail"),
    [
        ("<!-- the <code> tag -->", ""),
        ("<!-- use <pre> -->", ""),
        ('<img alt="<pre>" src="images/a.png">', ""),
        ("# The <code> element", ""),
        ("# The <code> element", "Use <code>x</code>."),
    ],
    ids=["comment-code", "comment-pre", "attribute", "unclosed-code", "code-past-its-paragraph"],
)
def test_an_opener_that_starts_no_code_hides_nothing_after_it(lead, tail):
    doc = f"{lead}\n\n![b](images/k.png)\n\n{tail}\n"
    assert "images/k.png" in _integrity.local_image_refs(doc, kind="markdown")


def test_a_code_span_inside_a_ref_target_hides_the_ref():
    """The span is read as code even inside a target, so the ref goes unseen; no placeholder leaks."""
    refs = _integrity.local_image_refs("# T\n\n![a](images/`x`.png)\n", kind="markdown")
    assert refs == set()


@pytest.mark.parametrize(
    "doc",
    [
        "<pre>\n// a<b isn't sorted\n</pre>\n\nThe menu's items:\n\n> Note.\n\n![b](images/k.png)\n",
        "Sort if a<b isn't.\n\n<pre>\n![a](images/x.png)\n</pre>\n\nIt's done -> next\n\n![b](images/k.png)\n",
        "<pre class=it's>\n![a](images/x.png)\n</pre>\n\n![b](images/k.png)\n",
    ],
    ids=["closer", "opener", "unquoted-value"],
)
def test_a_quote_opens_a_value_only_after_an_equals_sign(doc):
    assert _integrity.local_image_refs(doc, kind="markdown") == {"images/k.png"}


@pytest.mark.parametrize(
    ("lead", "trail"),
    [
        ("<!-->", ""),
        ("<!--->", ""),
        ("a <!-- b", ""),
        ("<!-->", "\n\n<!-- c -->"),
        ("<!--->", "\n\n<!-- c -->"),
    ],
    ids=["empty", "empty-dash", "unclosed", "empty-then-comment", "empty-dash-then-comment"],
)
def test_a_comment_ends_where_html_ends_it(lead, trail):
    """``<!-->`` and ``<!--->`` are whole comments, and a ``<!--`` that never closes is text."""
    doc = f"{lead}\n\n<pre>\n![a](images/x.png)\n</pre>\n\n![b](images/k.png){trail}\n"
    assert _integrity.local_image_refs(doc, kind="markdown") == {"images/k.png"}


@pytest.mark.parametrize(
    "doc",
    [
        "<pre><code>a</code>![a](images/x.png)</pre>\n\n![b](images/k.png)\n",
        "The <code> tag\r\n\r\n![b](images/k.png)\r\n\r\nUse <code>x</code>.\r\n",
        "</pre>\n\n![b](images/k.png)\n",
    ],
    ids=["closer-names-its-element", "crlf-paragraph", "stray-closer"],
)
def test_a_code_element_closes_only_on_its_own_closer(doc):
    assert _integrity.local_image_refs(doc, kind="markdown") == {"images/k.png"}


def test_an_unclosed_pre_runs_to_the_end():
    doc = '# T\n\n<pre>\n![a](images/x.png)\n\n<img src="images/b.png">\n'
    assert _integrity.local_image_refs(doc, kind="markdown") == {"images/b.png"}


@pytest.mark.parametrize(
    "text",
    [
        '<h1>Bk</h1><code>&lt;img src="images/a.png"&gt;</code>',
        '<video src="images/a.mp4"></video>',
    ],
    ids=["escaped-sample", "video"],
)
def test_a_file_whose_only_images_text_no_pass_writes_is_damaged(tmp_path, text):
    """Only a ref an image pass could have written explains a mismatch with no recorded hash."""
    slug_dir, _doc = _localized_slug(tmp_path, body=text)
    m = manifest.read_manifest(slug_dir)
    m["localized_sha256"], m["images"] = None, 0
    manifest.write_manifest(slug_dir, m)

    assert _state(slug_dir) == "damaged"
    assert _integrity.image_pass_ran(slug_dir, m, text) is False
    checks = {f["check"] for f in audit.audit_slug("bk")}
    assert "sha_mismatch" in checks and "sha_unverified" not in checks


def test_an_image_pass_ref_is_a_real_tags_or_a_markdown_target():
    assert _integrity._has_pass_ref('<IMG ALT="a" SRC="images/a.png">')
    assert _integrity._has_pass_ref("![a](images/a.png)")
    assert not _integrity._has_pass_ref('<code>&lt;img src="images/a.png"&gt;</code>')
    assert not _integrity._has_pass_ref('<video src="images/a.mp4"></video>')


@pytest.mark.parametrize(
    "hostile",
    [
        "<img " * 5000,
        '<img a="' * 5000,
        "<image " * 5000,
        '<img a="x<' * 5000,
        "<img a='" * 5000,
        "<img " + "'" * 60 + ">",
        "<img " + '"' * 60 + ">",
        "<pre>" * 5000,
        "<code " * 5000,
        "<pre>x</pre>" * 5000,
        "<!--" * 5000,
        "<code>x\n\n" * 5000,
        "<x 'q" * 5000,
        "<" + "a" * 50000,
    ],
)
def test_a_run_of_unclosed_tags_is_scanned_in_linear_time(hostile):
    started = time.monotonic()
    _integrity.local_image_refs(hostile, kind="markdown")
    assert time.monotonic() - started < 0.2


def _open_pass(slug_dir):
    _integrity.open_for_image_pass(slug_dir, manifest.read_manifest(slug_dir))


def _checkpoint_one_image(doc):
    (doc.parent / "images").mkdir(exist_ok=True)
    (doc.parent / "images" / "b.png").write_bytes(_PNG)
    text = doc.read_text(encoding="utf-8")
    doc.write_text(text.replace(_REMOTE[0], "images/b.png"), encoding="utf-8")


def _killed_after_a_checkpoint(slug_dir, doc):
    """What SIGKILL leaves once a pass has checkpointed one image: nothing after it ran."""
    _open_pass(slug_dir)
    _checkpoint_one_image(doc)


def _state(slug_dir):
    return _integrity.integrity(slug_dir, manifest.read_manifest(slug_dir))


def _checks(slug):
    return [(f["check"], f["level"]) for f in audit.audit_slug(slug)]


def test_a_pass_killed_before_its_first_write_leaves_the_file_intact(tmp_path):
    slug_dir, _doc = _localized_slug(tmp_path)

    _open_pass(slug_dir)

    assert _state(slug_dir) == "intact"


def test_a_pass_killed_after_a_checkpoint_resumes(tmp_path, images_served):
    slug_dir, doc = _localized_slug(tmp_path)
    _killed_after_a_checkpoint(slug_dir, doc)

    assert _state(slug_dir) == "interrupted"
    assert _integrity.deliverable_intact(slug_dir, manifest.read_manifest(slug_dir))
    res = localize.localize_images("bk")

    assert res["remaining"] == 0
    assert manifest.read_manifest(slug_dir)["localized_sha256"] == manifest.sha256_file(doc)
    assert _checks("bk") == []


@pytest.mark.parametrize(
    "ref",
    [
        f'<IMG SRC="{_REMOTE[0]}">',
        f"<img Src='{_REMOTE[0]}'>",
        f'![b]({_REMOTE[0]} "t")',
        '<img src="HTTPS://img.example/b.png">',
        '<img src="HTTPS://img.example/b.png">\n\n![b](HTTPS://img.example/b.png)',
    ],
    ids=["upper-src", "mixed-case-src", "titled-markdown", "upper-scheme", "upper-scheme-twice"],
)
def test_any_ref_pw_core_retargets_is_pass_progress(tmp_path, ref):
    slug_dir, doc = _localized_slug(tmp_path, body=f"<h1>Bk</h1>{ref}")
    _open_pass(slug_dir)
    (target,) = images.remote_image_refs(ref)

    doc.write_text(images._core._retarget(doc.read_text("utf-8"), target, "images/b.png"))

    assert "images/b.png" in doc.read_text(encoding="utf-8")
    assert _state(slug_dir) == "interrupted"


@pytest.mark.parametrize("damage", ["truncated", "edited"])
def test_damage_after_a_killed_pass_is_still_refused(tmp_path, images_served, damage):
    slug_dir, doc = _localized_slug(tmp_path)
    _killed_after_a_checkpoint(slug_dir, doc)
    text = doc.read_text(encoding="utf-8")
    doc.write_text(text[:40] if damage == "truncated" else text.replace("Bk", "Bx"), "utf-8")

    assert not _integrity.deliverable_intact(slug_dir, manifest.read_manifest(slug_dir))
    assert ("sha_mismatch", "error") in _checks("bk")
    with pytest.raises(PreconditionError, match="re-ingest"):
        localize.localize_images("bk")


class _RemoteImagesPattern:
    name = "fake"

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        (workdir / "raw").mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=workdir / "raw", kind="html", slug="bk", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / "bk.html"
        clean.write_text("".join(f'<img src="{u}">' for u in _REMOTE), encoding="utf-8")
        return clean


def test_an_ingest_killed_mid_image_pass_is_resumable(tmp_path, monkeypatch, images_served):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagesPattern())
    slug_dir = tmp_path / "incoming" / "bk"
    on_disk_at_kill = {}

    def killed_after_a_checkpoint(doc, images_dir, **kwargs):
        _checkpoint_one_image(doc)
        on_disk_at_kill["manifest"] = (slug_dir / manifest.MANIFEST_NAME).read_bytes()
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyboardInterrupt):
        mp.setattr(images, "download_images", killed_after_a_checkpoint)
        orchestrate.run_ingest("https://x", download_images=True)
    (slug_dir / manifest.MANIFEST_NAME).write_bytes(on_disk_at_kill["manifest"])

    assert _state(slug_dir) == "interrupted"
    assert localize.localize_images("bk")["remaining"] == 0
    assert _checks("bk") == []


def test_a_case_normalized_image_name_is_pass_progress(tmp_path):
    body = '<img src="images/Fig 1.PNG"><a href="images/Fig 1.PNG">Fig 1</a>'
    slug_dir, doc = _localized_slug(tmp_path, body)
    (slug_dir / "images" / "Fig 1.PNG").write_bytes(_PNG)
    _open_pass(slug_dir)

    _image_cache.normalize_case(doc, slug_dir)

    assert "images/fig 1.png" in doc.read_text(encoding="utf-8")
    assert _state(slug_dir) == "interrupted"


_STALLED_AFTER_A_CHECKPOINT = """
import sys, time
from functools import partial
from pathlib import Path

from pagespring import cli, http, images

started = Path(sys.argv[1])
images.download_images = partial(images.download_images, checkpoint_every=1)
fetched = []

def fetch(url, **kwargs):
    if fetched:
        started.write_text("checkpointed", encoding="utf-8")
        time.sleep(60)
        raise AssertionError("the kill never arrived")
    fetched.append(url)
    return url, b"\\x89PNG\\r\\n\\x1a\\nx", {"etag": None, "last_modified": None}

http.fetch_bytes_meta = fetch
http.polite_sleep = lambda *a, **k: None
sys.argv = ["pagespring", "localize", "bk"]
cli.main()
"""


def _run_until_checkpointed_then_kill(tmp_path):
    script = tmp_path / "stalled_localize.py"
    script.write_text(_STALLED_AFTER_A_CHECKPOINT, encoding="utf-8")
    started = tmp_path / "started"
    src = str(pathlib.Path(_integrity.__file__).resolve().parents[1])
    env = {**os.environ, "INCOMING_DIR": str(tmp_path / "incoming")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))
    proc = subprocess.Popen(
        [sys.executable, str(script), str(started)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 120  # generous: a loaded machine is slow to start Python
        while not started.exists():
            assert proc.poll() is None, proc.communicate()[1].decode()
            assert time.monotonic() < deadline, "the pass never reached its second fetch"
            time.sleep(0.02)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
    finally:
        proc.kill()
        proc.communicate()
    return proc.returncode


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX signals")
def test_a_sigkill_mid_pass_leaves_a_record_the_next_pass_resumes_from(tmp_path, images_served):
    slug_dir, doc = _localized_slug(tmp_path)

    assert _run_until_checkpointed_then_kill(tmp_path) == -signal.SIGKILL

    assert "images/b.png" in doc.read_text(encoding="utf-8")
    findings = _checks("bk")
    assert ("localize_interrupted", "warning") in findings
    assert not {"sha_mismatch", "sha_unverified"} & {check for check, _ in findings}
    assert localize.localize_images("bk")["remaining"] == 0
    assert manifest.read_manifest(slug_dir)["localized_sha256"] == manifest.sha256_file(doc)


def _staged_slug(tmp_path, body, *, local_from_staging):
    """A slug as ingest or renormalize staged it: its recorded ``sha256`` describes the file."""
    slug_dir = tmp_path / "incoming" / "bk"
    slug_dir.mkdir(parents=True)
    if local_from_staging:
        (slug_dir / "images").mkdir()
        (slug_dir / "images" / "a.png").write_bytes(_PNG)
    doc = slug_dir / "bk.html"
    doc.write_text(body, encoding="utf-8")
    sha = manifest.sha256_file(doc)
    manifest.write_manifest(
        slug_dir,
        manifest.build_manifest(
            source_url="https://x",
            pattern="fake",
            slug="bk",
            kind="html",
            deliverable="bk.html",
            pages=1,
            size_bytes=doc.stat().st_size,
            sha256=sha,
            images=1 if local_from_staging else 0,
            ingested_at="2026-11-14T00:00:00Z",
            localized_sha256=sha if local_from_staging else None,
        ),
    )
    return slug_dir, doc


def _kill_the_pass_before_it_writes(monkeypatch):
    def killed(deliverable, incoming_dir, kind):
        raise KeyboardInterrupt

    monkeypatch.setattr(localize, "_image_pass", killed)


def test_a_killed_pass_keeps_the_hash_of_a_file_staged_with_local_refs(tmp_path, monkeypatch):
    slug_dir, doc = _staged_slug(tmp_path, _BODY, local_from_staging=True)
    _kill_the_pass_before_it_writes(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        localize.localize_images("bk")

    m = manifest.read_manifest(slug_dir)
    assert m["localized_sha256"] == m["sha256"]
    doc.write_text(_BODY + "<p>damage</p>", encoding="utf-8")
    assert _state(slug_dir) == "damaged"


def test_a_killed_pass_leaves_an_unlocalized_file_without_a_localized_hash(tmp_path, monkeypatch):
    body = "<h1>Bk</h1>" + "".join(f'<img src="{u}">' for u in _REMOTE)
    slug_dir, _doc = _staged_slug(tmp_path, body, local_from_staging=False)
    _kill_the_pass_before_it_writes(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        localize.localize_images("bk")

    assert manifest.read_manifest(slug_dir)["localized_sha256"] is None
    assert _state(slug_dir) == "intact"
