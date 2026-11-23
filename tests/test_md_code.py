"""_md_code's inline spans: a backslash escapes a backtick outside a span and is text inside one,
and a run with no closer of its own length before the next blank line is text."""

import random
import re
import time

import pytest

from pagespring.patterns import _md_code

_IMG = "![b](images/k.png)"
# The scan as one regex: an escape, a span, or an unclosed run. ``_code_spans`` must find its spans.
_REFERENCE_RE = re.compile(
    r"\\[\s\S]|(?P<span>(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\2(?!`))|`+", re.DOTALL
)
_ATOMS = ["`", "`", "``", "```", "````", "\\", "\\", " ", "a", "\n", "\n\n", "\n \n", "\n\t\n"]


def _generated(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(_ATOMS) for _ in range(rng.randint(0, 24))) for _ in range(count)]


def test_code_spans_are_the_reference_scans():
    for doc in _generated(72, 3000):
        want = [m.span() for m in _REFERENCE_RE.finditer(doc) if m.group("span") is not None]
        assert _md_code._code_spans(doc) == want, repr(doc)


def test_outside_code_returns_the_text_an_identity_rewrite_is_given():
    for doc in _generated(73, 3000):
        assert _md_code.outside_code(doc, lambda text: text) == doc, repr(doc)


@pytest.mark.parametrize(
    "blank", ["\n\n", "\n \n", "\n\t\n", "\n \t \n"], ids=["bare", "space", "tab", "mixed"]
)
@pytest.mark.parametrize("before", ["", "x"], ids=["at-the-opener", "after-text"])
def test_a_blank_line_ends_a_span_wherever_it_falls(blank, before):
    md = f"a `{before}{blank}{_IMG} ` c\n"
    assert _IMG in _md_code.prose_outside_code(md)


@pytest.mark.parametrize(
    ("md", "is_prose"),
    [
        (f"a \\` b {_IMG} c ` d\n", True),
        (f"a \\\\`{_IMG}` d\n", False),
        (f"a \\``{_IMG}` d\n", False),
        (f"a \\`` b {_IMG} c ` d\n", False),
        (f"a \\``` b {_IMG} c ` d\n", True),
        (f"a `b\\` {_IMG} ` d\n", True),
        (f"a `` b {_IMG} ` c\n", True),
        (f"a `` b ` {_IMG} `` c\n", False),
        (f"a ` b\n\n{_IMG} ` c\n", True),
        (f"a \\\n`{_IMG}` d\n", False),
    ],
    ids=[
        "escaped-opener",
        "escaped-backslash-then-span",
        "escaped-backtick-then-span",
        "escaped-backtick-then-a-span-to-a-later-run",
        "escaped-backtick-then-a-run-without-its-closer",
        "backslash-inside-a-span-is-text",
        "run-without-its-closer",
        "shorter-run-inside-a-span",
        "span-does-not-cross-a-paragraph",
        "hard-break-backslash-then-span",
    ],
)
def test_which_backtick_runs_make_a_code_span(md, is_prose):
    assert (_IMG in _md_code.prose_outside_code(md)) is is_prose


def test_an_escaped_backtick_reaches_the_rewrite_as_written():
    seen: list[str] = []

    out = _md_code.outside_code("a \\` b [x](y) c ` d\n", lambda text: seen.append(text) or text)

    assert seen == ["a \\` b [x](y) c ` d\n"]
    assert out == "a \\` b [x](y) c ` d\n"


@pytest.mark.parametrize(
    "md",
    [
        "".join("`" * n + " x " for n in range(1, 600)),
        "\\`" * 20_000,
        "| \\`` | x |\n" * 5_000,
        "` x " * 10_000 + "`",
        "`" * 40_000,
    ],
    ids=[
        "runs-of-every-length",
        "escaped-backticks",
        "a-run-after-each-escape",
        "paired-spans",
        "one-long-run",
    ],
)
def test_the_span_scan_reads_backtick_runs_without_stalling(md):
    start = time.perf_counter()
    _md_code.prose_outside_code(md)
    assert time.perf_counter() - start < 1
