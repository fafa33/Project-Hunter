"""Fail-closed checks for the existing Issue-agent knowledge context CLI boundary."""

from __future__ import annotations

import argparse

import hunter_issue_agent_lifecycle as cli
import pytest


@pytest.mark.parametrize("path", ["/etc/passwd", "../secrets", "scripts/../../secrets", "scripts\\foo.py", ""])
def test_context_check_refuses_unsafe_paths_before_network(path: str) -> None:
    args = argparse.Namespace(path=path, checkout=".", base_sha="a" * 40)
    with pytest.raises(cli.LifecycleRefused, match="INVALID_SCOPE"):
        cli.cmd_knowledge_context_check(None, args)


def test_context_check_is_wired_to_existing_lifecycle_parser() -> None:
    args = cli._parser().parse_args(
        ["knowledge-context-check", "--path", "scripts/hunter_preflight_reuse_decision.py", "--base-sha", "a" * 40]
    )
    assert args.handler is cli.cmd_knowledge_context_check
