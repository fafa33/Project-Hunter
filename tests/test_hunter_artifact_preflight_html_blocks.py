"""CommonMark HTML-block masking regressions for the architecture-audit guard.

CommonMark section 4.6 defines seven HTML block types. Inside an HTML block the
contained lines are raw HTML, so Markdown structure is not interpreted there.
Semantic audit validation must therefore run on rendered structure, not on
source text that merely looks like Markdown.

Two directions matter equally: literal HTML-block content must not impersonate
audit structure, and ordinary inline HTML inside rendered prose must not erase
the surrounding rendered Markdown.
"""

from __future__ import annotations

import hunter_artifact_preflight
from test_hunter_artifact_preflight_semantics import _good_audit

ACCEPTED = ["0001", "0002"]


def _validate(text: str) -> list[str]:
    return hunter_artifact_preflight.validate_audit_text(text, accepted_adrs=ACCEPTED)


def _packed_audit() -> str:
    """A complete audit with no blank lines.

    A blank line terminates a CommonMark type 6/7 HTML block, so the hostile
    case is a container whose content never reaches one: every following line
    stays raw HTML.
    """
    return "\n".join(line for line in _good_audit().splitlines() if line.strip())


def test_entire_audit_hidden_in_a_div_block_cannot_satisfy_audit_structure() -> None:
    errors = _validate(f"<div>\n{_packed_audit()}\n</div>\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors
    assert any("Missing mandatory audit heading: ## Final Verdict" in error for error in errors), errors


def test_block_level_containers_cannot_impersonate_audit_structure() -> None:
    containers = ("div", "section", "article", "table", "blockquote", "form", "main", "aside", "details")
    packed = _packed_audit()
    for tag in containers:
        errors = _validate(f"<{tag}>\n{packed}\n</{tag}>\n")
        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), tag


def test_audit_sections_distributed_through_raw_containers_cannot_satisfy_structure() -> None:
    """Every section heading buried directly under a container line is raw HTML."""
    lines = []
    for line in _good_audit().splitlines():
        if line.startswith("## "):
            lines.append("<div>")
        lines.append(line)
    errors = _validate("\n".join(lines) + "\n")

    for heading in hunter_artifact_preflight.REQUIRED_AUDIT_HEADINGS:
        assert any(f"Missing mandatory audit heading: {heading}" in error for error in errors), heading


def test_previously_supported_raw_literal_elements_remain_masked() -> None:
    cases = (
        ("<pre>", "</pre>"),
        ('<SCRIPT type="text/plain">', "</SCRIPT>"),
        ('<style media="all">', "</style>"),
        ('<textarea name="audit">', "</textarea>"),
    )
    for opening, closing in cases:
        errors = _validate(f"{opening}\n{_good_audit()}\n{closing}\n")
        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), opening


def test_html_comments_remain_masked() -> None:
    errors = _validate(f"<!--\n{_good_audit()}\n-->\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_processing_instruction_declaration_and_cdata_blocks_are_masked() -> None:
    packed = _packed_audit()
    cases = (
        ("processing instruction", f"<?php echo 1;\n{packed}\n?>\n"),
        ("declaration", f"<!DOCTYPE\n{packed}\n>\n"),
        ("cdata", f"<![CDATA[\n{packed}\n]]>\n"),
    )
    for label, document in cases:
        errors = _validate(document)
        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), label


def test_self_terminating_declaration_does_not_swallow_the_following_audit() -> None:
    """`<!DOCTYPE html>` closes on its own line, so CommonMark resumes Markdown.

    Masking past the terminator would be over-broad and would reject a
    canonically valid rendered audit.
    """
    assert _validate(f"<!DOCTYPE html>\n{_good_audit()}") == []


def test_inline_html_inside_rendered_prose_does_not_erase_audit_structure() -> None:
    text = _good_audit().replace(
        "Full independent architecture audit.",
        "Full <em>independent</em> architecture audit using <code>src/hunter/</code> evidence.",
    )

    assert _validate(text) == []


def test_inline_html_opening_a_line_mid_paragraph_does_not_start_a_block() -> None:
    text = _good_audit().replace(
        "Full independent architecture audit.",
        "Full independent architecture audit.\n<em>Emphasis</em> continues the same paragraph.",
    )

    assert _validate(text) == []


def test_real_rendered_audit_adjacent_to_raw_html_decoys_still_passes() -> None:
    packed = _packed_audit().replace("Jules", "PENDING")
    decoys = (
        f"\n<div>\n{packed}\n</div>\n",
        f"\n<pre>\n{packed}\n</pre>\n",
        f"\n<!--\n{packed}\n-->\n",
        f"\n<![CDATA[\n{packed}\n]]>\n",
    )
    for decoy in decoys:
        assert _validate(_good_audit() + decoy) == [], decoy


def test_html_block_inside_a_fence_does_not_corrupt_fence_state() -> None:
    fenced = "\n```html\n<div>\n<pre>\n</div>\n```\n\nRendered prose resumes here.\n"

    assert _validate(_good_audit() + fenced) == []


def test_completion_checklist_hidden_in_a_raw_block_does_not_count() -> None:
    good = _good_audit()
    items = "\n".join(line for line in good.splitlines() if line.startswith("- [x] "))
    hostile = good.replace(items, f"<div>\n{items}")

    errors = _validate(hostile)
    assert any("must not be empty: ## Audit Completion Check" in error for error in errors), errors


def test_blank_terminated_container_does_not_suppress_the_content_after_it() -> None:
    """A blank line closes a type 6 block, so the checklist below it really renders."""
    good = _good_audit()
    loose = good.replace("## Audit Completion Check\n", "## Audit Completion Check\n\n<div>\n")

    assert _validate(loose) == []


def test_literal_tag_names_in_rendered_prose_and_tables_are_not_containers() -> None:
    text = _good_audit().replace(
        "| Problem correctness | PASS | Evidence-backed. | F-001 |",
        "| Problem correctness | PASS | Reviewed <div> emission in output. | F-001 |",
    )

    assert _validate(text) == []


def test_type_seven_block_after_an_atx_heading_hides_following_structure() -> None:
    """A type 7 block may not interrupt a paragraph, but a heading is not a paragraph.

    After an ATX heading the parser is between blocks, so a bare tag on the next
    line legitimately opens a type 7 HTML block and everything up to the next
    blank line is raw HTML.
    """
    errors = _validate(f"# Decoy\n<x>\n{_packed_audit()}\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_type_seven_block_after_a_rendered_level_two_heading_hides_structure() -> None:
    body = "\n".join(line for line in _good_audit().splitlines() if line.strip())
    errors = _validate(f"## Some rendered heading\n<custom-tag>\n{body}\n")

    assert any("Missing mandatory audit heading: ## Final Verdict" in error for error in errors), errors


def test_type_seven_block_after_a_fenced_block_hides_structure() -> None:
    errors = _validate(f"```\ncode\n```\n<x>\n{_packed_audit()}\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_type_seven_tag_may_not_interrupt_an_active_paragraph() -> None:
    """`<x>` inside a paragraph is inline HTML, so the paragraph keeps rendering."""
    text = _good_audit().replace(
        "Full independent architecture audit.",
        "Full independent architecture audit\n<x>\ncontinues in the same paragraph.",
    )

    assert _validate(text) == []


def test_inline_type_seven_tag_within_prose_remains_rendered_prose() -> None:
    text = _good_audit().replace(
        "Full independent architecture audit.",
        "Full independent <x>architecture</x> audit.",
    )

    assert _validate(text) == []


def test_blank_line_started_type_seven_block_remains_masked() -> None:
    errors = _validate(f"Intro paragraph.\n\n<x>\n{_packed_audit()}\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_real_rendered_audit_adjacent_to_a_type_seven_decoy_still_passes() -> None:
    decoy = f"\n<x>\n{_packed_audit().replace('Jules', 'PENDING')}\n"

    assert _validate(_good_audit() + decoy) == []


def test_type_seven_tag_inside_a_fence_does_not_corrupt_block_state() -> None:
    fenced = "\n```html\n<x>\n<custom-tag>\n```\n\nRendered prose resumes here.\n"

    assert _validate(_good_audit() + fenced) == []
