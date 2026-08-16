import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_draft_promotion_signal


def test_scheduled_reconciliation_isolates_one_malformed_draft_and_continues():
    drafts = [
        {"number": 275, "draft": True, "state": "open", "base": {"ref": "main"}},
        {"number": 276, "draft": True, "state": "open", "base": {"ref": "main"}},
    ]

    with (
        patch("hunter_draft_promotion_signal.open_draft_prs", return_value=drafts),
        patch(
            "hunter_draft_promotion_signal.evaluate",
            side_effect=[RuntimeError("invalid readiness declaration"), None],
        ) as evaluate,
    ):
        with pytest.raises(RuntimeError, match="PR #275: RuntimeError: invalid readiness declaration"):
            hunter_draft_promotion_signal.reconcile_open_draft_prs()

    assert [call.args[0]["number"] for call in evaluate.call_args_list] == [275, 276]
