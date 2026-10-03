import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hunter_governance_review_v2 import native_copilot_verdict  # noqa: E402

DETAILS = """<details>
<summary>Review details</summary>

- **Files reviewed:** 2/2 changed files
- **Comments generated:** 0 new
- **Review effort level:** Lite
</details>"""


def test_accepts_observed_clean_copilot_shape_with_metadata():
    body = "### 🟢 Approval recommended\n\nNo unresolved blocking issues were identified.\n\n" + DETAILS
    assert native_copilot_verdict(body) == "clear"


def test_accepts_observed_no_comments_shape_with_metadata():
    body = "### 🟢 Approval recommended\n\nNo unresolved review comments remain.\n\n" + DETAILS
    assert native_copilot_verdict(body) == "clear"


def test_rejects_contradictory_suffix_after_valid_metadata():
    body = (
        "### 🟢 Approval recommended\n\nNo unresolved blocking issues were identified.\n\n"
        + DETAILS
        + "\n\nAdditional blocking finding."
    )
    assert native_copilot_verdict(body) != "clear"


def test_rejects_unrecognized_metadata_suffix():
    body = (
        "### 🟢 Approval recommended\n\nNo unresolved blocking issues were identified.\n\n<details>unexpected</details>"
    )
    assert native_copilot_verdict(body) != "clear"


def test_inline_comment_always_blocks():
    body = "### 🟢 Approval recommended\n\nNo unresolved blocking issues were identified.\n\n" + DETAILS
    assert native_copilot_verdict(body, inline_comment_count=1) == "blocking"


# Exact authenticated Copilot review body observed on PR #553 head
# c7e7b6d9907697c10e8ebb225070f295b31bb178 (zero inline findings).
PR553_OVERVIEW_BODY = (
    "<!-- ccr-overview-v2 -->\n\n"
    "## Copilot review overview\n\n"
    "### 🔵 Needs a closer look\n\n"
    "Root-of-trust signing-key changes require final human review.\n\n"
    "**Review effort:** Lite  \n"
    "**Findings:** None"
)


def test_pr553_overview_v2_with_findings_none_and_no_inline_comments_is_clear():
    assert native_copilot_verdict(PR553_OVERVIEW_BODY, inline_comment_count=0) == "clear"
    assert native_copilot_verdict(PR553_OVERVIEW_BODY) == "clear"


def test_pr553_overview_v2_with_an_inline_finding_blocks():
    assert native_copilot_verdict(PR553_OVERVIEW_BODY, inline_comment_count=1) == "blocking"


def test_overview_v2_nonzero_findings_count_is_not_clear():
    body = PR553_OVERVIEW_BODY.replace("**Findings:** None", "**Findings:** 2")
    assert native_copilot_verdict(body) != "clear"


def test_overview_v2_changes_recommended_is_not_clear():
    body = PR553_OVERVIEW_BODY.replace("### 🔵 Needs a closer look", "### 🔴 Changes recommended")
    assert native_copilot_verdict(body) != "clear"


def test_overview_v2_contradictory_second_findings_line_is_not_clear():
    assert native_copilot_verdict(PR553_OVERVIEW_BODY + "\n**Findings:** 1") != "clear"
    assert native_copilot_verdict(PR553_OVERVIEW_BODY + "\n**Findings:** None") != "clear"


def test_overview_v2_quoted_findings_none_with_real_nonzero_findings_is_not_clear():
    body = PR553_OVERVIEW_BODY.replace(
        "Root-of-trust signing-key changes require final human review.",
        'The reviewer wrote "Findings: None" earlier.',
    ).replace("**Findings:** None", "**Findings:** 3")
    assert native_copilot_verdict(body) != "clear"
    quoted_only = PR553_OVERVIEW_BODY.replace(
        "Root-of-trust signing-key changes require final human review.",
        "> **Findings:** None",
    ).replace("**Review effort:** Lite  \n**Findings:** None", "**Review effort:** Lite  \n**Findings:** 3")
    assert native_copilot_verdict(quoted_only) != "clear"


def test_overview_v2_trailing_content_after_findings_is_not_clear():
    assert native_copilot_verdict(PR553_OVERVIEW_BODY + "\n\nAdditional blocking finding.") != "clear"
    assert native_copilot_verdict(PR553_OVERVIEW_BODY + "\n\n<details>unexpected</details>") != "clear"


def test_overview_v2_malformed_or_unknown_formats_are_not_clear():
    no_marker = PR553_OVERVIEW_BODY.replace("<!-- ccr-overview-v2 -->\n\n", "")
    wrong_marker = PR553_OVERVIEW_BODY.replace("ccr-overview-v2", "ccr-overview-v3")
    no_findings = PR553_OVERVIEW_BODY.replace("\n**Findings:** None", "")
    no_heading = PR553_OVERVIEW_BODY.replace("## Copilot review overview\n\n", "")
    indented_marker = PR553_OVERVIEW_BODY.replace("<!-- ccr-overview-v2 -->", "    <!-- ccr-overview-v2 -->")
    for body in (no_marker, wrong_marker, no_findings, no_heading, indented_marker, "", "Findings: None"):
        assert native_copilot_verdict(body) != "clear", body
