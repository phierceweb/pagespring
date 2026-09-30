"""_mdx — JSX stripped from MDX source, prose kept."""

import time

import pytest

from pagespring.patterns._mdx import mdx_to_markdown


def test_a_wrapper_component_loses_its_tags_and_keeps_its_prose():
    md = "Intro.\n\n<AppOnly>\n\nLearn about [caching](/docs/caching).\n\n</AppOnly>\n\nAfter.\n"

    assert mdx_to_markdown(md) == "Intro.\n\nLearn about [caching](/docs/caching).\n\nAfter.\n"


def test_inline_components_keep_their_text_and_stay_apart():
    md = "Allow access to your <PagesOnly>API Endpoints</PagesOnly><AppOnly>Route Handlers</AppOnly>.\n"

    assert mdx_to_markdown(md) == "Allow access to your API Endpoints Route Handlers.\n"


def test_an_inline_component_with_a_prop_keeps_only_its_children():
    md = 'Stored in your <Tooltip tip="Your docs\' source code, where files live.">repository</Tooltip>.\n'

    assert mdx_to_markdown(md) == "Stored in your repository.\n"


def test_a_self_closing_component_with_multiline_props_is_dropped():
    md = (
        "Before.\n\n"
        "<Image\n"
        '  alt="page.js > special file"\n'
        '  srcLight="/docs/light/page.png"\n'
        "  width={1600}\n"
        "/>\n\n"
        "After.\n"
    )

    assert mdx_to_markdown(md) == "Before.\n\nAfter.\n"


def test_rows_of_strings_passed_as_a_prop_become_a_table():
    md = (
        "<ApiTable\n"
        "  rows={[\n"
        '    ["z-<number>", "z-index: <number>;"],\n'
        "    ['z-auto', 'z-index: auto;'],\n"
        "  ]}\n"
        "/>\n\n"
        "## Examples\n"
    )

    assert mdx_to_markdown(md) == (
        "<table>\n"
        "<tr><td>z-&lt;number&gt;</td><td>z-index: &lt;number&gt;;</td></tr>\n"
        "<tr><td>z-auto</td><td>z-index: auto;</td></tr>\n"
        "</table>\n\n"
        "## Examples\n"
    )


def test_rows_built_by_code_are_not_a_table():
    md = '<ApiTable rows={[["a", "b"], ...Object.entries(colors).map(([k, v]) => [k, v])]} />\n'

    assert mdx_to_markdown(md) == "\n"


def test_mdx_comments_are_dropped():
    md = "{/* single */}\nText.\n\n{/* spans\n   two lines */}\n\nMore.\n"

    assert mdx_to_markdown(md) == "Text.\n\nMore.\n"


def test_a_rendered_demo_inside_an_expression_is_dropped():
    md = (
        "Stack order:\n\n"
        "<Example>\n"
        "  {\n"
        '    <div className="flex">\n'
        '      <div className="z-40">05</div>\n'
        "    </div>\n"
        "  }\n"
        "</Example>\n\n"
        "```html\n"
        '<div class="z-40">05</div>\n'
        "```\n"
    )

    assert mdx_to_markdown(md) == ('Stack order:\n\n```html\n<div class="z-40">05</div>\n```\n')


def test_markup_in_an_expression_outside_any_component_is_kept():
    md = (
        "Every variant:\n\n"
        "{\n\n"
        "<table>\n"
        "  <tr>\n"
        '    <td className="x">hover</td>\n'
        '    <td>{"&:hover"}</td>\n'
        "  </tr>\n"
        "</table>\n\n"
        "}\n\n"
        "After.\n"
    )

    assert mdx_to_markdown(md) == (
        "Every variant:\n\n"
        "<table>\n"
        "  <tr>\n"
        '    <td class="x">hover</td>\n'
        "    <td>&amp;:hover</td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "After.\n"
    )


def test_code_in_an_expression_outside_any_component_is_dropped():
    md = "Text.\n\n{items.map((item) => <Row key={item} />)}\n\nMore.\n"

    assert mdx_to_markdown(md) == "Text.\n\nMore.\n"


def test_a_fragment_expression_keeps_its_text():
    md = "<TipBad>{<>Traditionally the same class applies on <em>hover</em></>}</TipBad>\n"

    assert mdx_to_markdown(md) == "Traditionally the same class applies on <em>hover</em>\n"


def test_a_string_literal_expression_becomes_its_text():
    md = 'Use <code>min-[{">="}320px]</code> and a{" "}space.\n'

    assert mdx_to_markdown(md) == "Use <code>min-[&gt;=320px]</code> and a space.\n"


def test_html_elements_stay_with_jsx_only_syntax_removed():
    md = (
        "<table>\n"
        "  <tr>\n"
        '    <td className="whitespace-nowrap" style={{ width: "1%" }}>\n'
        "      <code>--color-*</code>\n"
        "    </td>\n"
        "  </tr>\n"
        "</table>\n"
    )

    assert mdx_to_markdown(md) == (
        "<table>\n"
        "  <tr>\n"
        '    <td class="whitespace-nowrap">\n'
        "      <code>--color-*</code>\n"
        "    </td>\n"
        "  </tr>\n"
        "</table>\n"
    )


def test_code_fences_and_code_spans_are_left_as_written():
    md = (
        "Wrap it in `<PagesOnly>Content</PagesOnly>` or ``{x}``.\n\n"
        '```tsx filename="app/layout.tsx"\n'
        "export default function Layout({ children }) {\n"
        "  return <Main>{/* Layout UI */}{children}</Main>\n"
        "}\n"
        "```\n"
    )

    assert mdx_to_markdown(md) == md


def test_indentation_that_only_nested_children_is_removed():
    """MDX has no indented code blocks; left in place, the indent turns prose into code."""
    md = (
        "<Tabs>\n"
        '  <Tab title="CLI">\n'
        "    <Steps>\n"
        '      <Step title="Install the CLI">\n'
        "        Requires Node.js.\n"
        "\n"
        "        1. Run:\n"
        "\n"
        "           ```bash\n"
        "           npm i -g mint\n"
        "           ```\n"
        "      </Step>\n"
        "    </Steps>\n"
        "  </Tab>\n"
        "</Tabs>\n"
    )

    assert mdx_to_markdown(md) == (
        "**CLI**\n\n"
        "**Install the CLI**\n\n"
        "Requires Node.js.\n\n"
        "1. Run:\n\n"
        "   ```bash\n"
        "   npm i -g mint\n"
        "   ```\n"
    )


def test_a_block_component_title_becomes_a_bold_line():
    md = '<Accordion title="Skip the Git provider">\nYou can skip it.\n</Accordion>\n'

    assert mdx_to_markdown(md) == "**Skip the Git provider**\n\nYou can skip it.\n"


def test_a_self_closing_card_keeps_its_title():
    md = (
        "The overlay offers this fix:\n\n"
        "<FixCardGrid>\n"
        "  <FixCard\n"
        '    title="Wrap in Suspense"\n'
        "    snippets={[{ text: '<Suspense>' }]}\n"
        "  />\n"
        "</FixCardGrid>\n"
    )

    assert mdx_to_markdown(md) == "The overlay offers this fix:\n\n**Wrap in Suspense**\n"


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        (
            '<CardGrid>\n  <LinkCard title="Ghost tutorial" href="https://example.com/g" />\n'
            "</CardGrid>\n",
            "**[Ghost tutorial](https://example.com/g)**\n",
        ),
        (
            "<Card title='Use the editor' icon=\"pen\" horizontal href='/editor'>\n"
            "  Edit in the browser.\n</Card>\n",
            "**[Use the editor](/editor)**\n\nEdit in the browser.\n",
        ),
    ],
)
def test_a_card_title_links_to_its_href(md, expected):
    assert mdx_to_markdown(md) == expected


def test_an_inline_component_title_is_not_emitted():
    md = 'See <Badge title="beta" /> and <Tip title="x">tip</Tip> here.\n'

    assert mdx_to_markdown(md) == "See  and tip here.\n"


@pytest.mark.parametrize(
    "md",
    [
        "Compare a <Foo and b.\n",
        "An { unclosed brace.\n",
        "A <Bar\n\nnever closes>.\n",
    ],
)
def test_markup_that_never_closes_is_left_as_text(md):
    assert mdx_to_markdown(md) == md


def test_an_escaped_brace_stays_literal():
    md = "Write \\{name\\} to interpolate.\n"

    assert mdx_to_markdown(md) == md


def test_imports_and_exports_are_dropped_and_an_exported_title_heads_the_page():
    md = (
        'import { Example } from "@/components/example.tsx";\n'
        "\n"
        'export const title = "z-index";\n'
        'export const description = "Utilities for controlling the stack order.";\n'
        "\n"
        "## Examples\n"
    )

    assert mdx_to_markdown(md) == (
        "# z-index\n\nUtilities for controlling the stack order.\n\n## Examples\n"
    )


def test_an_exported_title_does_not_add_a_second_h1():
    md = 'export const title = "Push";\n\n# `drizzle-kit push`\n\nText.\n'

    assert mdx_to_markdown(md) == "# `drizzle-kit push`\n\nText.\n"


def test_a_heading_in_a_code_fence_is_not_the_page_title():
    md = 'export const title = "Install";\n\n```bash\n# install it\n```\n'

    assert mdx_to_markdown(md).startswith("# Install\n")


def test_a_source_comment_and_front_matter_are_kept_as_written():
    head = "<!-- source: https://x/y.mdx -->\n\n---\ntitle: Layouts\ndescription: Wrap {x} in <Link>\n---\n"
    md = head + "\n<AppOnly>\nText.\n</AppOnly>\n"

    assert mdx_to_markdown(md) == head + "\nText.\n"


def test_crlf_line_endings_are_handled():
    md = "<Callout>\r\n  Note text.\r\n</Callout>\r\n"

    assert mdx_to_markdown(md) == "Note text.\n"


def test_a_blank_line_before_a_tag_closes_is_still_one_tag():
    md = "<CodeBlock\n  example={x}\n\n/>\n\nAfter.\n"

    assert mdx_to_markdown(md) == "After.\n"


def test_a_code_sample_passed_as_a_tagged_template_becomes_a_fenced_block():
    md = (
        "<CodeExampleGroup>\n"
        "  <CodeBlock\n"
        "    example={css`\n"
        "      .group-\\[\\.is-published\\]\\:block {\n"
        "        display: block;\n"
        "      }\n"
        "    `}\n"
        "  />\n"
        "</CodeExampleGroup>\n"
    )

    assert mdx_to_markdown(md) == (
        "```css\n.group-\\[\\.is-published\\]\\:block {\n  display: block;\n}\n```\n"
    )


def test_a_multiline_import_is_dropped_whole():
    md = (
        'import { ApiTable } from "@/components/api-table.tsx";\n'
        "import {\n"
        "  ResponsiveDesign,\n"
        "  UsingACustomValue,\n"
        '} from "@/components/content.tsx";\n'
        "\n"
        "## Examples\n\n"
        "```js\nimport {\n  a,\n} from 'kept-in-code';\n```\n"
    )

    assert mdx_to_markdown(md) == (
        "## Examples\n\n```js\nimport {\n  a,\n} from 'kept-in-code';\n```\n"
    )


def test_a_leading_rule_is_not_front_matter():
    md = "---\n\n<Note>\nText.\n</Note>\n\n---\n"

    assert mdx_to_markdown(md) == "---\n\nText.\n\n---\n"


@pytest.mark.parametrize(
    "md",
    ["Text {oops\n" * 4000, "<Code sample={css`" + "a${" * 8000],
    ids=["prose", "template-hole"],
)
def test_braces_that_never_close_are_found_in_linear_time(md):
    """Each unclosed `{` stays text; finding that out must not rescan the rest of the page."""
    start = time.perf_counter()
    out = mdx_to_markdown(md)

    assert time.perf_counter() - start < 2
    assert out.count("{") == md.count("{")
