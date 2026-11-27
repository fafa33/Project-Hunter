"""Issue #575: owner [test:...] evidence must be parsed in bounded linear time.

The previous ``_OWNER_TEST_TAG`` pattern used two unbounded greedy classes around
``::``.  An owner reply with many colons and no closing bracket made the regex
backtrack super-linearly.  These regressions preserve the selector contract while
requiring explicit length/count bounds and linear scanning.
"""

from __future__ import annotations

import time

import pytest

from hunter.automation import issue_agent_control as control


def test_valid_owner_test_tags_preserve_the_selector_contract() -> None:
    body = (
        "Confirmed. [family:DFF-080] "
        "[test:tests/test_a.py::test_one] "
        "[TEST:TESTS/TEST_B.PY::TEST_TWO] "
        "Exact-head proof succeeded."
    )
    assert control.owner_test_selectors(body) == (
        "tests/test_a.py::test_one",
        "TESTS/TEST_B.PY::TEST_TWO",
    )


def test_malformed_and_overlong_owner_test_tags_are_ignored() -> None:
    body = (
        "[test:no-separator] "
        "[test::missing-left] "
        "[test:missing-right::] "
        f"[test:{'x' * 600}::test_long] "
        "[test:valid.py::test_valid]"
    )
    assert control.owner_test_selectors(body) == ("valid.py::test_valid",)


def test_adversarial_owner_reply_input_is_bounded_and_linear() -> None:
    body = "[test:" + ("a:" * 40_000) + "[test:" + ("b:" * 40_000)
    started = time.monotonic()
    assert control.owner_test_selectors(body) == ()
    assert time.monotonic() - started < 1.0


def test_unclosed_owner_test_tag_does_not_backtrack() -> None:
    body = "[test:" + ("a::" * 30_000)
    started = time.monotonic()
    assert control.owner_test_selectors(body) == ()
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize(
    "body",
    [
        "[test:a.py::test_a][test:a.py::test_a][test:a.py::test_a]",
        "[test:a.py::test_a] [test:a.py::test_a] [test:a.py::test_a] extra ]",
        "[test:a.py::test_a::part] [test:a.py::test_b] [test:c.py::test_c]",
    ],
)
def test_selector_contract_keeps_multiple_selectors_and_extra_colons(body: str) -> None:
    selectors = control.owner_test_selectors(body)
    assert selectors[0].startswith("a.py::")
    assert len(selectors) == 3
