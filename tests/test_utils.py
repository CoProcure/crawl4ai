import pytest

from crawl4ai.utils import normalize_url


@pytest.mark.parametrize(
    ("href", "base_url", "expected"),
    [
        ("path/to/page.html", "http://example.com/base/", "http://example.com/base/path/to/page.html"),
        ("page.html", "http://example.com/base", "http://example.com/page.html"),
        ("http://another.com/page.html", "http://example.com/", "http://another.com/page.html"),
        ("  page.html  ", "http://example.com/", "http://example.com/page.html"),
        ("page.html?query=test", "http://example.com/", "http://example.com/page.html?query=test"),
        ("../otherpage.html", "http://example.com/base/current/", "http://example.com/base/otherpage.html"),
        ("/otherpage.html", "http://example.com/base/current/", "http://example.com/otherpage.html"),
        ("?query=true", "http://example.com/page.html", "http://example.com/page.html?query=true"),
        ("path with spaces/file.html", "http://example.com/", "http://example.com/path%20with%20spaces/file.html"),
    ],
    ids=[
        "relative_path",
        "base_without_trailing_slash",
        "absolute_href",
        "surrounding_spaces",
        "query_parameters",
        "parent_directory",
        "root_relative",
        "query_only",
        "spaces_in_path",
    ],
)
def test_normalize_url_resolves_href_against_base_url(href, base_url, expected):
    assert normalize_url(href, base_url) == expected
