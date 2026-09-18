"""No statement may sit after an unconditional exit in the block it belongs to.

The cost of this shape is highest in the governance controllers, which are the
root of trust: what they refuse is the only thing standing between a candidate
and admission. A validation block placed after a `return` still reads exactly
like enforcement -- same checks, same `return None` on failure -- while running
never. PR #473 shipped that into `hunter_governance_review_v2`'s
`review_result_observation`, where a duplicated tail appeared to require a
substantive reviewer summary and could not. The same shape in a test is a test
that quietly stops asserting, so the guard covers the sources it can.

Reviewers do not reliably see it, and neither Ruff's selected rules nor Black
report it. Mypy's `--warn-unreachable` does, but it reasons from declared types
and so also condemns the deliberate `else` arms these modules use to reject
runtime values that contradict an annotation -- deleting those would be a
weakening, not a fix. The defect is syntactic, so this reads the syntax: a
statement that follows `return`, `raise`, `continue` or `break` in the same
block, which is never anything but a mistake.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("scripts", "src", "tests")

TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _sources() -> list[Path]:
    return sorted(path for root in SOURCE_ROOTS for path in (ROOT / root).rglob("*.py"))


def dead_statements(tree: ast.AST) -> list[tuple[int, str]]:
    """Every statement that can never execute because its block already exited."""
    dead: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, statement in enumerate(block):
                if isinstance(statement, TERMINATORS):
                    dead.extend((follower.lineno, type(follower).__name__) for follower in block[index + 1 :])
                    break
    return dead


def test_no_source_file_carries_unreachable_statements() -> None:
    offenders = []
    for source in _sources():
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # not this guard's boundary; the linters own unparseable files
        dead = dead_statements(tree)
        if dead:
            offenders.append(f"{source.relative_to(ROOT)}: lines {[line for line, _ in dead]}")
    assert offenders == [], "unreachable statements found:\n" + "\n".join(offenders)


def test_the_guard_reads_control_flow_rather_than_text() -> None:
    """Paired fixtures: the shape that shipped, and the shapes that must not trip."""
    shipped = ast.parse(
        "def f(value):\n"
        "    if not value:\n"
        "        return None\n"
        "    return value\n"
        "\n"
        "    if not value.get('claims_id'):\n"
        "        return None\n"
        "    return value\n"
    )
    assert [kind for _line, kind in dead_statements(shipped)] == ["If", "Return"]

    # A `return` ending one branch does not kill the sibling branch, the code
    # after the whole `if`, the next function, or a later loop iteration.
    for accepted in (
        "def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n",
        "def f(x):\n    if x:\n        return 1\n    return 2\n",
        "def f(x):\n    return 1\n\n\ndef g(x):\n    return 2\n",
        "def f(xs):\n    for x in xs:\n        if x:\n            continue\n        print(x)\n    return None\n",
        "def f(x):\n    try:\n        return 1\n    finally:\n        print('cleanup')\n",
        "def f(x):\n    while x:\n        break\n    return x\n",
    ):
        assert dead_statements(ast.parse(accepted)) == [], accepted
