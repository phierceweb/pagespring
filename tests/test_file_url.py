"""_file_url — which URLs a host-specific pattern leaves to a single-file pattern."""

from __future__ import annotations

import pytest

from pagespring.patterns._file_url import owned_by_file_pattern


@pytest.mark.parametrize(
    "url",
    [
        "https://x.example/s/article/manual.pdf",
        "https://x.example/docs/guide.zip",
        "https://x.example/api/spec.yaml",
        "https://x.example/api/openapi.JSON",
    ],
)
def test_a_pdf_archive_or_spec_url_belongs_to_its_file_pattern(url):
    assert owned_by_file_pattern(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.example/s/article/Widget-Pro-Setup-v1.2",
        "https://x.example/docs/openapi.html",
        "https://x.example/s/topic/0TO3b000000gYW1GAM",
    ],
)
def test_a_page_url_is_left_to_the_host_pattern(url):
    assert not owned_by_file_pattern(url)
