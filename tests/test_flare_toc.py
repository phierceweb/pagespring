"""_flare_toc: Flare's TOC and chunk files read as data, and the tree walked in reading order."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring.patterns import _flare_toc

_TOC = (
    "define({numchunks:2,prefix:'Guide_Chunk',"
    "chunkstart:['/Content/A.htm','/Content/M.htm'],"
    "tree:{n:[{i:0,c:0},{i:1,c:1,n:[{i:2,c:0}]}]}});"
)


def test_the_toc_names_its_chunk_files():
    toc = _flare_toc.read_toc(_TOC, source="Guide.js")

    assert toc.chunk_files == ["Guide_Chunk0.js", "Guide_Chunk1.js"]


def test_a_chunk_maps_every_placement_of_a_key_to_its_title():
    chunk = (
        "define({'/Content/A.htm':{i:[0,7],t:['Alpha','Alpha again'],b:['','']},"
        "'___':{i:[3],t:['Book'],b:['']}});"
    )

    assert _flare_toc.read_chunk(chunk, 0, source="Guide_Chunk0.js") == {
        (0, 0): ("/Content/A.htm", "Alpha"),
        (0, 7): ("/Content/A.htm", "Alpha again"),
        (0, 3): ("___", "Book"),
    }


def test_titles_and_keys_decode_their_escapes():
    chunk = (
        "define({ '/Content/It\\u0027s here.htm' : { i : [ 4 ] ,"
        " t : [ 'Don\\u0027t \\'stop\\' \\\\ \"now\"' ] , b : [ '' ] } });"
    )

    assert _flare_toc.read_chunk(chunk, 2, source="c.js") == {
        (2, 4): ("/Content/It's here.htm", "Don't 'stop' \\ \"now\""),
    }


@pytest.mark.parametrize(
    "text",
    [
        "var toc = {numchunks:1};",
        "define({numchunks:1,prefix:'P',tree:{n:[{i:0,c:0}",
        "define({numchunks:1,prefix:'P'});",
        "define({numchunks:'x',prefix:'P',tree:{n:[]}});",
        "define({numchunks:1,prefix:'P',tree:{n:[{i:0,c:0}]}}) trailing junk(",
        "define({numchunks:100000,prefix:'P',tree:{n:[]}});",
    ],
)
def test_a_toc_that_is_not_flare_shaped_is_refused(text):
    with pytest.raises(InvalidInputError, match="Guide.js"):
        _flare_toc.read_toc(text, source="Guide.js")


def test_a_toc_cut_off_before_a_value_says_so():
    with pytest.raises(InvalidInputError, match="expected a value"):
        _flare_toc.read_toc("define(", source="Guide.js")


def test_a_chunk_that_is_not_flare_shaped_is_refused():
    with pytest.raises(InvalidInputError, match="c0.js"):
        _flare_toc.read_chunk("define({'/Content/A.htm':{i:[0],t:[]}});", 0, source="c0.js")


def test_deep_nesting_is_refused_rather_than_exhausting_the_stack():
    text = "define({numchunks:1,prefix:'P',tree:{n:" + "[" * 5000 + "]" * 5000 + "}});"

    with pytest.raises(InvalidInputError, match="Guide.js"):
        _flare_toc.read_toc(text, source="Guide.js")


def _walk(tree: str, placements: dict) -> list[tuple[int, str, str | None]]:
    toc = _flare_toc.read_toc(
        f"define({{numchunks:1,prefix:'P',chunkstart:[],tree:{{n:{tree}}}}});", source="t.js"
    )
    return [(e.depth, e.title, e.path) for e in _flare_toc.toc_entries(toc, placements)]


def test_entries_follow_the_tree_in_reading_order_with_their_depth():
    placements = {
        (0, 0): ("/Content/A.htm", "Alpha"),
        (1, 1): ("/Content/Sub/B.htm", "Beta"),
        (0, 2): ("/Content/Sub/C.htm", "Gamma"),
    }

    assert _walk("[{i:0,c:0},{i:1,c:1,n:[{i:2,c:0}]}]", placements) == [
        (0, "Alpha", "Content/A.htm"),
        (0, "Beta", "Content/Sub/B.htm"),
        (1, "Gamma", "Content/Sub/C.htm"),
    ]


def test_a_book_without_a_link_heads_the_topics_under_it():
    placements = {(0, 0): ("___", "Getting Started"), (0, 1): ("/Content/Setup.htm", "Setup")}

    assert _walk("[{i:0,c:0,n:[{i:1,c:0}]}]", placements) == [
        (0, "Getting Started", None),
        (1, "Setup", "Content/Setup.htm"),
    ]


def test_links_out_of_the_manual_are_not_pages_and_head_nothing():
    placements = {
        (0, 0): ("https://www.vendor.example/products/widget/", "Product page"),
        (0, 1): ("/Content/Files/datasheet.pdf", "Datasheet"),
        (0, 2): ("___", "Elsewhere"),
        (0, 3): ("https://www.vendor.example/store/", "Store"),
        (0, 4): ("/Content/Intro.htm", "Intro"),
    }

    tree = "[{i:0,c:0,f:'_blank'},{i:1,c:0},{i:2,c:0,n:[{i:3,c:0,f:'_blank'}]},{i:4,c:0}]"

    assert _walk(tree, placements) == [(0, "Intro", "Content/Intro.htm")]


@pytest.mark.parametrize("key", ["/Content/Setup.htm#Wiring", "/Content/Setup.htm?x=1#Wiring"])
def test_a_link_to_a_bookmark_in_a_topic_names_that_topic(key):
    placements = {(0, 0): ("___", "Setup"), (0, 1): (key, "Wiring")}

    assert _walk("[{i:0,c:0,n:[{i:1,c:0}]}]", placements) == [
        (0, "Setup", None),
        (1, "Wiring", "Content/Setup.htm"),
    ]


def test_a_bookmark_to_a_topic_the_tree_also_links_is_not_where_the_topic_is_staged():
    placements = {
        (0, 0): ("___", "Intro"),
        (0, 1): ("/Content/Setup.htm#Wiring", "See Wiring"),
        (0, 2): ("/Content/Setup.htm", "Setup"),
        (0, 3): ("/Content/Wiring.htm", "Wiring"),
    }
    tree = "[{i:0,c:0,n:[{i:1,c:0}]},{i:2,c:0},{i:1,c:0,n:[{i:3,c:0}]}]"

    assert _walk(tree, placements) == [
        (0, "Setup", "Content/Setup.htm"),
        (0, "See Wiring", None),
        (1, "Wiring", "Content/Wiring.htm"),
    ]


def test_a_node_its_chunk_does_not_title_keeps_its_children():
    placements = {(0, 1): ("/Content/Orphan.htm", "Orphan")}

    assert _walk("[{i:0,c:0,n:[{i:1,c:0}]}]", placements) == [(1, "Orphan", "Content/Orphan.htm")]
