"""_toc_stage: a TOC staged in reading order, nesting kept across skipped topics (no network)."""

from dataclasses import dataclass

import pytest

from pagespring import http
from pagespring.patterns._toc_stage import Section, TopicLost, stage_toc


@dataclass(frozen=True)
class _Item:
    depth: int
    title: str
    page: str | None = None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _stage(tmp_path, items, bodies, max_pages=100):
    def fetch(item):
        body = bodies[item.page]
        if body is None:
            raise TopicLost(f"{item.page} is gone")
        return Section(body, body, item.page, item.page) if body else None

    staged = stage_toc(
        items, tmp_path, key=lambda i: i.page, fetch=fetch, max_pages=max_pages, event="t"
    )
    return staged, [p.read_text(encoding="utf-8") for p in sorted(tmp_path.glob("*.html"))]


def test_sections_are_staged_in_order_under_the_headings_above_them(tmp_path):
    items = [_Item(0, "Book"), _Item(1, "A", "a"), _Item(1, "B", "b")]

    staged, sections = _stage(tmp_path, items, {"a": "<p>A</p>", "b": "<p>B</p>"})

    assert (staged.pages, staged.lost, staged.stalled) == (2, 0, False)
    assert sections[0] == "<!-- source: a -->\n<section>\n<h1>Book</h1>\n<p>A</p>\n</section>\n"
    assert "Book" not in sections[1]


@pytest.mark.parametrize("skip", ["lost", "empty", "duplicate", "repeated"])
def test_a_skipped_parent_heads_the_topics_under_it_and_a_skipped_leaf_heads_nothing(
    tmp_path, skip
):
    bodies = {"x": "<p>X</p>", "a": {"lost": None, "empty": ""}.get(skip, "<p>X</p>")}
    bodies |= {"c": "<p>C</p>", "leaf": bodies["a"], "z": "<p>Z</p>"}
    first = "a" if skip == "repeated" else "x"
    items = [
        _Item(0, "X", first),
        _Item(0, "A", "a"),
        _Item(1, "C", "c"),
        _Item(1, "Leaf", "a" if skip == "repeated" else "leaf"),
        _Item(0, "Z", "z"),
    ]

    staged, sections = _stage(tmp_path, items, bodies)

    gamma = next(s for s in sections if "<p>C</p>" in s)
    assert gamma.index("<h1>A</h1>") < gamma.index("<p>C</p>")
    assert all("Leaf" not in s for s in sections)
    assert staged.lost == (2 if skip == "lost" else 0)


def test_the_page_cap_stops_before_the_next_fetch(tmp_path):
    items = [_Item(0, t, t) for t in "abc"]

    staged, sections = _stage(tmp_path, items, {t: f"<p>{t}</p>" for t in "abc"}, max_pages=2)

    assert staged.pages == 2 and all("<p>c</p>" not in s for s in sections)
