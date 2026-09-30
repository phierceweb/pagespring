"""_ordering — the natural sort key patterns share."""

from pathlib import Path

from pagespring.patterns._ordering import natural_key


def test_embedded_numbers_compare_numerically():
    names = ["ch10.md", "ch2.md", "ch1.md", "appendix.md"]
    assert sorted(names, key=lambda n: natural_key(Path(n))) == [
        "appendix.md",
        "ch1.md",
        "ch2.md",
        "ch10.md",
    ]


def test_case_does_not_split_an_order():
    names = ["Beta.md", "alpha.md", "Gamma.md"]
    assert sorted(names, key=lambda n: natural_key(Path(n))) == ["alpha.md", "Beta.md", "Gamma.md"]


def test_numbers_compare_within_each_directory_level():
    paths = [Path("part10/a.md"), Path("part2/b.md"), Path("part2/a.md")]
    assert sorted(paths, key=natural_key) == [
        Path("part2/a.md"),
        Path("part2/b.md"),
        Path("part10/a.md"),
    ]
