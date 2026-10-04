"""Drift gate: the root llms.txt must list every shipped doc by raw URL; CLAUDE.md and
CODE_OF_CONDUCT.md are not docs. ``test_llms_txt.py`` is the pattern's suite."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_ROOT_DOCS = ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md")


def test_llms_txt_lists_every_shipped_doc():
    text = (ROOT / "llms.txt").read_text()
    expected = [f"/{name}" for name in _ROOT_DOCS if (ROOT / name).exists()]
    expected += [f"/docs/{p.name}" for p in sorted((ROOT / "docs").glob("*.md"))]
    missing = [path for path in expected if path not in text]
    assert not missing, "docs missing from llms.txt:\n" + "\n".join(missing)


def test_llms_txt_links_are_absolute():
    for line in (ROOT / "llms.txt").read_text().splitlines():
        if "](" in line:
            url = line.split("](", 1)[1].split(")", 1)[0]
            assert url.startswith("https://"), f"non-absolute link: {url}"
