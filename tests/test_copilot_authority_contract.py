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
