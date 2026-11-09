import pytest

from crawl4ai import DefaultMarkdownGenerator


def to_markdown(html: str, **options) -> str:
    generator = DefaultMarkdownGenerator(options=options or None)
    return generator.generate_markdown(html, base_url="https://example.com").raw_markdown


@pytest.mark.parametrize(
    "line", ["1. not a list", "+ not a bullet", "- not a bullet", "--- not a rule"]
)
def test_line_starts_that_look_like_list_markers_are_not_escaped(line):
    html = f"<p>{line}</p>"

    markdown = to_markdown(html)

    assert markdown.strip() == line


def test_backslashes_are_still_escaped():
    html = r"<p>C:\path\to\file</p>"

    markdown = to_markdown(html)

    assert markdown.strip() == r"C:\path\to\file"


def test_definition_list_puts_each_definition_on_an_indented_line_under_its_term():
    html = "<dl><dt>Term</dt><dd>Definition</dd><dt>Other</dt><dd>Second</dd></dl>"

    markdown = to_markdown(html)

    assert markdown == "\n\nTerm\n    Definition\n\nOther\n    Second\n"


def test_definition_list_does_not_swallow_the_paragraph_after_it():
    html = "<dl><dt>A</dt><dd>one</dd></dl><p>after</p>"

    markdown = to_markdown(html)

    assert markdown.endswith("A\n    one\nafter\n")


def test_code_inside_link_keeps_backticks_inside_the_link_text():
    html = '<a href="/docs"><code>foo()</code></a>'

    markdown = to_markdown(html)

    assert markdown.strip() == "[`foo()`](https://example.com/docs)"


def test_code_outside_link_is_wrapped_in_backticks():
    html = "<p>run <code>foo()</code> now</p>"

    markdown = to_markdown(html)

    assert markdown.strip() == "run `foo()` now"


def test_link_state_is_cleared_after_the_link_closes():
    html = '<a href="/docs">docs</a> <code>x</code>'

    markdown = to_markdown(html)

    assert markdown.strip().endswith("`x`")


def test_pre_with_nested_code_becomes_a_fenced_block():
    html = "<pre><code>a\n  b</code></pre>"

    markdown = to_markdown(html)

    assert markdown.strip() == "```\na\n  b\n```"


def test_mailto_links_are_dropped_by_default_and_kept_when_enabled():
    html = '<a href="mailto:a@b.com">Email</a>'

    default_markdown = to_markdown(html)
    enabled_markdown = to_markdown(html, ignore_mailto_links=False)

    assert default_markdown.strip() == "Email"
    assert enabled_markdown.strip() == "[Email](mailto:a@b.com)"
