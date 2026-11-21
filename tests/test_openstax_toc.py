"""The OpenStax book tree read from a REX page's embedded state, and the headings it places."""

import json

import pytest

from pagespring.patterns import _openstax_toc as toc
from pagespring.patterns._openstax_toc import (
    TREE_NAME,
    book_tree,
    chapter_number,
    heading_plan,
    part_slug,
    raw_headings,
    toc_nodes,
)


def _page(slug: str, title: str, target: str) -> dict:
    return {
        "id": slug,
        "title": title,
        "toc_type": "book-content",
        "toc_target_type": target,
        "slug": slug,
    }


def _numbered(number: str, divider: str, text: str) -> str:
    return (
        f'<span class="os-number">{number}</span>\n<span class="os-divider">{divider}</span>\n'
        f'<span class="os-text" data-type="" itemprop="">{text}</span>'
    )


def _chapter(n: int, text: str, pages: list[dict]) -> dict:
    return {
        "id": f"ch{n}",
        "title": _numbered(str(n), " ", text),
        "toc_type": "chapter",
        "slug": f"{n}-chapter",
        "contents": pages,
    }


def _tree() -> dict:
    """OpenStax's node shapes: preface, a unit around two chapters, appendices, an answer-key
    sub-book-tree, a composite-page References and an index."""
    ch1 = _chapter(
        1,
        "Functions and Graphs",
        [
            _page("1-introduction", "Introduction", "intro"),
            _page("1-1-review-of-functions", "1.1 Review of Functions", "numbered-section"),
            {
                "id": "r1",
                "title": '<span class="os-text">Chapter Review</span>',
                "toc_type": "sub-book-tree",
                "slug": "1-chapter-review",
                "contents": [_page("1-key-terms", "Key Terms", "composite-page")],
            },
        ],
    )
    ch2 = _chapter(
        2,
        "Limits &amp; Continuity &lt;Part&gt;",
        [
            _page("2-introduction", "Introduction", "intro"),
            _page("2-1-a-preview-of-calculus", "2.1 A Preview", "numbered-section"),
        ],
    )
    return {
        "id": "book",
        "title": "Calculus Volume 1",
        "contents": [
            _page("preface", "Preface", "preface"),
            {
                "id": "u1",
                "title": "Foundations",
                "toc_type": "unit",
                "slug": "u1",
                "contents": [ch1, ch2],
            },
            _page("a-table-of-integrals", _numbered("A", " | ", "Table of Integrals"), "appendix"),
            _page(
                "b-table-of-derivatives", _numbered("B", " | ", "Table of Derivatives"), "appendix"
            ),
            {
                "id": "ak",
                "title": "Answer Key",
                "toc_type": "sub-book-tree",
                "slug": "answer-key",
                "contents": [
                    _page("chapter-1", "Chapter 1", "answer-key"),
                    _page("chapter-2", "Chapter 2", "answer-key"),
                ],
            },
            _page("references", "References", "composite-page"),
            _page("index", "Index", "index"),
        ],
    }


_SLUGS = [
    "preface",
    "1-introduction",
    "1-1-review-of-functions",
    "1-key-terms",
    "2-introduction",
    "2-1-a-preview-of-calculus",
    "a-table-of-integrals",
    "b-table-of-derivatives",
    "chapter-1",
    "chapter-2",
    "references",
    "index",
]


def _state_page(tree: dict) -> str:
    state = {
        "content": {"book": {"title": "Calculus Volume 1", "version": "8dbc2ce", "tree": tree}}
    }
    # REX escapes "<" in the embedded JSON so the script cannot close early.
    payload = json.dumps(state).replace("<", "\\u003c")
    return (
        '<!DOCTYPE html><html><body><main class="page-content"><p>body</p></main>'
        f"<script>window.__PRELOADED_STATE__ = {payload};</script></body></html>"
    )


def _nodes() -> list[toc.TocNode]:
    nodes = toc_nodes(_tree())
    assert nodes is not None
    return nodes


@pytest.mark.parametrize(
    ("slug", "number"),
    [
        ("9-6-forces-and-torques", 9),
        ("9-introduction-to-statics", 9),
        ("12-key-terms", 12),
        ("chapter-9", None),
        ("a-atomic-masses", None),
        ("preface", None),
        ("index", None),
        ("references", None),
    ],
)
def test_chapter_number(slug, number):
    assert chapter_number(slug) == number


def test_part_slug_reads_the_source_line():
    part = "<!-- source: https://openstax.org/books/x/pages/9-6-forces -->\n<section>\n</section>\n"
    assert part_slug(part) == "9-6-forces"
    assert part_slug("<section></section>") is None
    assert part_slug("<!-- source: https://openstax.org/books/x -->\n") is None


def test_book_tree_reads_the_embedded_state():
    assert book_tree(_state_page(_tree())) == _tree()


def test_book_tree_absent_or_broken_is_none():
    assert book_tree("<html><body><main>no state</main></body></html>") is None
    assert book_tree("<script>window.__PRELOADED_STATE__ = {broken</script>") is None
    assert book_tree('<script>window.__PRELOADED_STATE__ = {"content": {}}</script>') is None
    no_contents = '{"content": {"book": {"tree": {"title": "x"}}}}'
    assert book_tree(f"<script>window.__PRELOADED_STATE__ = {no_contents}</script>") is None


def test_units_flatten_into_their_chapters():
    nodes = _nodes()
    assert [n.toc_type for n in nodes] == [
        "book-content",
        "chapter",
        "chapter",
        "book-content",
        "book-content",
        "sub-book-tree",
        "book-content",
        "book-content",
    ]
    assert nodes[1].slugs == ("1-introduction", "1-1-review-of-functions", "1-key-terms")
    assert nodes[5].slugs == ("chapter-1", "chapter-2")


def test_titles_flatten_spans_and_keep_markup_characters_as_text():
    nodes = _nodes()
    assert nodes[1].title == "1 Functions and Graphs"
    assert nodes[1].number == "1"
    assert nodes[2].title == "2 Limits & Continuity <Part>"
    assert nodes[3].title == "A | Table of Integrals"
    assert nodes[5].title == "Answer Key"
    assert nodes[5].number is None


def test_unknown_node_type_makes_the_tree_unreadable():
    tree = _tree()
    tree["contents"][1]["contents"][0]["contents"].append(
        {"id": "x", "title": "Review", "toc_type": "eoc-dropdown", "contents": []}
    )
    assert toc_nodes(tree) is None
    tree = _tree()
    tree["contents"].append({"id": "y", "title": "Extra", "toc_type": "mystery"})
    assert toc_nodes(tree) is None


def test_heading_plan_places_chapters_and_back_matter():
    plan = heading_plan(list(_SLUGS), _nodes())
    assert plan == [
        (1, "1 Functions and Graphs"),
        (4, "2 Limits & Continuity <Part>"),
        (6, "A | Table of Integrals"),
        (7, "B | Table of Derivatives"),
        (8, "Answer Key"),
        (10, "References"),
        (11, "Index"),
    ]


def test_heading_plan_without_a_preface_heads_the_first_chapter_at_part_zero():
    plan = heading_plan(_SLUGS[1:], _nodes())
    assert plan is not None
    assert plan[0] == (0, "1 Functions and Graphs")


class _Spy:
    def __init__(self):
        self.events = []

    def warning(self, event, **kw):
        self.events.append((event, kw))

    def info(self, event, **kw):
        self.events.append((event, kw))


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()
    monkeypatch.setattr(toc, "log", s)
    return s


def _reasons(spy: _Spy) -> list[str]:
    return [kw["reason"] for event, kw in spy.events if event == "openstax.tree_mismatch"]


def test_heading_plan_refuses_a_staged_page_missing_from_the_tree(spy):
    slugs = [*_SLUGS[:3], "1-2-stray-page", *_SLUGS[3:]]
    assert heading_plan(slugs, _nodes()) is None
    assert _reasons(spy) == ["page_not_in_tree"]


def test_heading_plan_refuses_nodes_out_of_order(spy):
    slugs = [*_SLUGS[:4], "a-table-of-integrals", *_SLUGS[4:6], *_SLUGS[7:]]
    assert heading_plan(slugs, _nodes()) is None
    assert _reasons(spy) == ["out_of_order"]


def test_heading_plan_refuses_a_chapter_whose_number_differs_from_its_slugs(spy):
    tree = _tree()
    tree["contents"][1]["contents"][1]["title"] = _numbered("3", " ", "Limits")
    nodes = toc_nodes(tree)
    assert nodes is not None
    assert heading_plan(list(_SLUGS), nodes) is None
    assert _reasons(spy) == ["chapter_number"]


def test_heading_plan_refuses_a_numbered_page_outside_any_chapter(spy):
    tree = _tree()
    tree["contents"].insert(1, _page("0-stray", "Stray", "composite-page"))
    nodes = toc_nodes(tree)
    assert nodes is not None
    assert heading_plan(["preface", "0-stray", *_SLUGS[1:]], nodes) is None
    assert _reasons(spy) == ["chapter_number"]


def test_tree_pages_missing_from_the_crawl_do_not_block(spy):
    slugs = [s for s in _SLUGS if s != "1-key-terms" and s != "references"]
    plan = heading_plan(slugs, _nodes())
    assert plan is not None
    assert "References" not in [title for _i, title in plan]
    assert ("openstax.tree_pages_not_staged", {"count": 2, "first": "1-key-terms"}) in spy.events


def _parts(slugs: list[str]) -> list[str]:
    return [
        f"<!-- source: https://openstax.org/books/calculus-volume-1/pages/{s} -->\n<section>\n"
        "</section>\n"
        for s in slugs
    ]


def test_raw_headings_reads_the_kept_tree(tmp_path):
    (tmp_path / TREE_NAME).write_text(json.dumps(_tree()), encoding="utf-8")
    assert raw_headings(tmp_path, _parts(_SLUGS))[0] == (1, "1 Functions and Graphs")


def test_raw_headings_without_a_tree_is_empty(tmp_path, spy):
    assert raw_headings(tmp_path, _parts(_SLUGS)) == []
    assert spy.events == []


def test_raw_headings_over_an_unreadable_tree_is_empty_and_logged(tmp_path, spy):
    (tmp_path / TREE_NAME).write_text("{not json", encoding="utf-8")
    assert raw_headings(tmp_path, _parts(_SLUGS)) == []
    assert _reasons(spy) == ["unreadable_tree"]


@pytest.mark.parametrize("contents", [5, "pages", None, {"a": 1}])
def test_a_unit_whose_contents_is_not_a_list_makes_the_tree_unreadable(contents):
    unit = {"id": "u", "title": "U", "toc_type": "unit", "contents": contents}
    assert toc_nodes({"contents": [unit]}) is None
    assert toc_nodes({"contents": contents}) is None


@pytest.mark.parametrize("number", ["²", "IV", "1.5", ""])
def test_a_chapter_number_int_cannot_read_is_refused_not_raised(number, spy):
    tree = _tree()
    tree["contents"][1]["contents"][1]["title"] = _numbered(number, " ", "Limits")
    nodes = toc_nodes(tree)
    assert nodes is not None
    assert heading_plan(list(_SLUGS), nodes) is None
    assert _reasons(spy) == ["chapter_number"]


def test_heading_plan_refuses_a_chapter_without_a_number_over_unnumbered_pages(spy):
    tree = _tree()
    tree["contents"].insert(
        1,
        {
            "id": "c0",
            "title": '<span class="os-text">Getting Started</span>',
            "toc_type": "chapter",
            "contents": [_page("getting-started", "Getting Started", "intro")],
        },
    )
    nodes = toc_nodes(tree)
    assert nodes is not None
    assert heading_plan(["preface", "getting-started", *_SLUGS[1:]], nodes) is None
    assert _reasons(spy) == ["chapter_number"]


def _nested(kind: str, depth: int) -> dict:
    node: dict = _page("1-introduction", "Introduction", "intro")
    for _ in range(depth):
        node = {"title": _numbered("1", " ", "Deep"), "toc_type": kind, "contents": [node]}
    return node


def test_state_json_nested_past_the_decoder_limit_is_no_tree():
    deep = "[" * 200_000
    assert book_tree(f"<script>window.__PRELOADED_STATE__ = {deep}</script>") is None


@pytest.mark.parametrize("kind", ["chapter", "unit"])
def test_a_tree_nested_deeper_than_any_book_is_unreadable_not_a_crash(kind):
    assert toc_nodes({"contents": [_nested(kind, 1200)]}) is None
    assert toc_nodes({"contents": [_nested(kind, 3)]}) is not None


def test_a_kept_tree_nested_past_the_decoder_limit_reads_as_unreadable(tmp_path, spy):
    (tmp_path / TREE_NAME).write_text(
        '{"contents": ' + "[" * 200_000 + "]" * 200_000 + "}", encoding="utf-8"
    )
    assert raw_headings(tmp_path, _parts(_SLUGS)) == []
    assert _reasons(spy) == ["unreadable_tree"]


@pytest.mark.parametrize("title", ["", "   ", "<span></span>"])
def test_a_top_level_node_without_title_text_makes_the_tree_unreadable(title):
    tree = _tree()
    tree["contents"][2]["title"] = title  # appendix A
    assert toc_nodes(tree) is None


def test_heading_plan_refuses_a_page_the_tree_places_under_two_nodes(spy):
    tree = _tree()
    answer_key = tree["contents"][4]["contents"]
    answer_key.append(_page("1-introduction", "Introduction", "answer-key"))
    nodes = toc_nodes(tree)
    assert nodes is not None
    assert heading_plan(list(_SLUGS), nodes) is None
    assert _reasons(spy) == ["page_in_two_nodes"]


def test_a_preface_that_does_not_open_the_book_gets_a_heading():
    tree = _tree()
    preface = tree["contents"].pop(0)
    tree["contents"].insert(1, preface)  # after the unit holding chapters 1-2
    nodes = toc_nodes(tree)
    assert nodes is not None
    slugs = [*_SLUGS[1:6], "preface", *_SLUGS[6:]]
    plan = heading_plan(slugs, nodes)
    assert plan is not None
    assert plan[0] == (0, "1 Functions and Graphs")
    assert (5, "Preface") in plan


def _deep_state_page(kind: str, depth: int) -> str:
    leaf = json.dumps(_page("1-introduction", "Introduction", "intro"))
    node = f'{{"title": "x", "toc_type": "{kind}", "contents": ['
    tree = '{"contents": [' + node * depth + leaf + "]}" * depth + "]}"
    return f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree}}}}}}}</script>'


@pytest.mark.parametrize("kind", ["chapter", "unit"])
def test_state_nested_deeper_than_any_book_is_no_tree(kind):
    assert book_tree(_deep_state_page(kind, 600)) is None
    assert book_tree(_deep_state_page(kind, 3)) is not None


def _state_with(tree_json: str) -> str:
    return f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree_json}}}}}}}</script>'


_LEAF = '{"title": "Preface", "toc_type": "book-content", "toc_target_type": "preface", "slug": "preface"}'


def _lists_under(key: str, depth: int) -> str:
    """A real-shaped tree plus ``depth`` nested lists under ``key``; the root dict is depth 0."""
    nested = "[" * depth + "]" * depth
    if key == "contents":
        return f'{{"contents": [{_LEAF}, {nested[1:-1] or "[]"}]}}'
    return f'{{"contents": [{_LEAF}], "{key}": {nested}}}'


@pytest.mark.parametrize("key", ["contents", "extra", "title"])
def test_book_tree_refuses_json_nested_past_the_cap_under_any_key(key):
    cap = 2 * toc._MAX_NESTING
    assert book_tree(_state_with(_lists_under(key, cap))) is not None
    assert book_tree(_state_with(_lists_under(key, cap + 1))) is None


@pytest.mark.parametrize("kind", ["chapter", "unit"])
def test_book_tree_cap_on_a_node_chain(kind):
    assert book_tree(_deep_state_page(kind, toc._MAX_NESTING - 1)) is not None
    assert book_tree(_deep_state_page(kind, toc._MAX_NESTING)) is None


@pytest.mark.parametrize(
    "tree_json",
    [
        f'{{"contents": [{_LEAF}], "extra": "\\ud800"}}',
        '{"contents": [{"title": "Pre\\udc00face", "toc_type": "book-content", "slug": "preface"}]}',
        f'{{"contents": [{_LEAF}], "\\udfff": 1}}',
    ],
    ids=["value", "title", "key"],
)
def test_book_tree_refuses_a_lone_surrogate_utf8_cannot_encode(tree_json):
    assert book_tree(_state_with(tree_json)) is None
    assert (
        book_tree(
            _state_with(
                tree_json.replace("\\ud800", "x").replace("\\udc00", "").replace("\\udfff", "k")
            )
        )
        is not None
    )


def test_a_kept_tree_holding_a_lone_surrogate_reads_as_unreadable(tmp_path, spy):
    tree = _tree()
    tree["contents"][2]["title"] = "A | Table \ud800 of Integrals"
    (tmp_path / TREE_NAME).write_text(json.dumps(tree), encoding="utf-8")  # ensure_ascii escapes it
    assert raw_headings(tmp_path, _parts(_SLUGS)) == []
    assert _reasons(spy) == ["unreadable_tree"]
