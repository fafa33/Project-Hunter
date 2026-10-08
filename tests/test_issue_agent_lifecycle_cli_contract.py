"""Workflow-to-CLI contract of the GitHub-native Issue Agent lifecycle (Issue #574).

The first live ``execute`` job could not have started: the workflow passes
``--model-argv /opt/hunter/bin/opencode run --model "$MODEL"`` and argparse resolved the model command's own ``--model``
by abbreviation to the ambiguous pair ``--model-key-name`` / ``--model-argv``. These tests parse the *real* invocations
out of the workflow files with the real parser, so a mismatch between the two is caught before a hosted run.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import hunter_issue_agent_candidate_pr as candidate_pr
import hunter_issue_agent_lifecycle as lifecycle
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
LIFECYCLE_WORKFLOWS = (
    "hunter-issue-agent-trigger.yml",
    "hunter-issue-agent-reconcile.yml",
    "hunter-issue-agent-candidate-pr.yml",
    "hunter-issue-agent-knowledge.yml",
    "hunter-issue-agent-source-handling-bootstrap.yml",
)
INTEGER_OPTIONS = {"--issue", "--artifact-id", "--pr"}
VALID_CHOICE = {"--stage": "validation"}


def _runs(workflow: str) -> list[tuple[str, str]]:
    document = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return [
        (f"{workflow}:{job_id}:{step.get('name', '')}", str(step["run"]))
        for job_id, job in document["jobs"].items()
        for step in job.get("steps", [])
        if "run" in step
    ]


def _argv(invocation: str) -> list[str]:
    """Shell words of one invocation with every ``$VAR`` / ``$(...)`` replaced by a value of the right type."""

    flattened = re.sub(r"\\\n\s*", " ", invocation)
    flattened = re.sub(r"\$\([^)]*\)", "PLACEHOLDER", flattened)
    words = shlex.split(flattened)
    resolved: list[str] = []
    for index, word in enumerate(words):
        if "$" in word:
            previous = words[index - 1] if index else ""
            word = "1" if previous in INTEGER_OPTIONS else VALID_CHOICE.get(previous, "placeholder-value")
        resolved.append(word)
    return resolved


def _lifecycle_invocations() -> list[tuple[str, list[str]]]:
    found = []
    for workflow in LIFECYCLE_WORKFLOWS:
        for where, run in _runs(workflow):
            for match in re.finditer(r"hunter_issue_agent_lifecycle\.py\s+((?:[^\n\\]|\\\n)+)", run):
                found.append((where, _argv(match.group(1))))
    return found


def test_every_lifecycle_invocation_in_every_workflow_parses_with_the_real_parser() -> None:
    invocations = _lifecycle_invocations()
    assert invocations, "no lifecycle invocation found in the workflows"
    for where, argv in invocations:
        try:
            lifecycle.parse_arguments(argv)
        except SystemExit as error:  # argparse exits instead of raising
            pytest.fail(f"{where}: {' '.join(argv)} is rejected by the lifecycle parser (exit {error.code})")


def test_the_workflows_exercise_every_lifecycle_subcommand() -> None:
    parser = lifecycle._parser()
    declared = next(a for a in parser._actions if a.dest == "command").choices
    invoked = {argv[0] for _, argv in _lifecycle_invocations() if not argv[0].startswith("-")}
    assert set(declared) == invoked


def _execute_argv() -> list[str]:
    runs = dict(_runs("hunter-issue-agent-trigger.yml"))
    (run,) = (v for k, v in runs.items() if k.endswith("Run the model once through isolation and seal the result"))
    return _argv(re.search(r"hunter_issue_agent_lifecycle\.py\s+((?:[^\n\\]|\\\n)+)", run).group(1))  # type: ignore[union-attr]


def test_the_exact_execute_command_forwards_the_model_command_to_opencode_unchanged() -> None:
    argv = _execute_argv()
    assert "--model" in argv  # the workflow really does pass the downstream option that used to be ambiguous
    arguments = lifecycle.parse_arguments(argv)
    tail = argv[argv.index("--model-argv") + 1 :]
    assert tail[:2] == ["/opt/hunter/bin/opencode", "run"] and tail[2] == "--model"
    assert arguments.model_argv == tuple(tail)
    # The lifecycle's own required options are still parsed as lifecycle options, not swallowed by the tail.
    assert (arguments.command, arguments.issue, arguments.model_key_name) == ("execute", 1, "placeholder-value")
    assert arguments.handoff and arguments.out and arguments.workroot


EXECUTE_HEAD = [
    "execute",
    "--issue",
    "574",
    "--authorization-id",
    "hunter-issue-agent-authorization:aa",
    "--handoff",
    "h",
    "--out",
    "o",
    "--workroot",
    "w",
    "--model-key-name",
    "K",
]


def test_the_model_command_is_preserved_verbatim_including_lifecycle_lookalikes() -> None:
    tail = [
        "/opt/hunter/bin/opencode",
        "run",
        "--model",
        "p/m",
        "--out",
        "not-ours",
        "--issue",
        "9",
        "--model-argv",
        "x",
    ]
    arguments = lifecycle.parse_arguments([*EXECUTE_HEAD, "--model-argv", *tail])
    assert arguments.model_argv == tuple(tail)
    assert (arguments.out, arguments.issue, arguments.model_key_name) == ("o", 574, "K")


@pytest.mark.parametrize(
    "abbreviation",
    [
        ["--model-key", "K"],
        ["--model-arg", "x"],
        ["--model", "x"],
        ["--work", "w"],
        ["--handof", "h"],
        ["--authorization", "a"],
    ],
)
def test_no_lifecycle_option_is_accepted_by_abbreviation(abbreviation: list[str]) -> None:
    head = [a for a in EXECUTE_HEAD if a not in {"--model-key-name", "K"}]
    with pytest.raises(SystemExit):
        lifecycle.parse_arguments([*head, *abbreviation])


@pytest.mark.parametrize(
    "drop",
    ["--issue", "--authorization-id", "--handoff", "--out", "--workroot", "--model-key-name"],
)
def test_each_required_lifecycle_option_must_still_be_supplied(drop: str) -> None:
    head = list(EXECUTE_HEAD)
    index = head.index(drop)
    del head[index : index + 2]
    with pytest.raises(SystemExit):
        lifecycle.parse_arguments([*head, "--model-argv", "/opt/hunter/bin/opencode", "run"])


def test_a_lifecycle_option_placed_after_the_tail_cannot_be_smuggled_in() -> None:
    head = [a for a in EXECUTE_HEAD if a not in {"--out", "o"}]
    # --out only appears inside the model command, so the lifecycle's own requirement stays unmet.
    with pytest.raises(SystemExit):
        lifecycle.parse_arguments([*head, "--model-argv", "/opt/hunter/bin/opencode", "run", "--out", "o"])


def test_a_missing_or_empty_model_command_fails_safely() -> None:
    for argv in (
        EXECUTE_HEAD,
        [*EXECUTE_HEAD, "--model-argv"],
        [*EXECUTE_HEAD, "--model-argv", "--"],
        [*EXECUTE_HEAD, "--model-argv", ""],
    ):
        with pytest.raises(SystemExit) as error:
            lifecycle.parse_arguments(argv)
        assert error.value.code == 2
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle._model_command([" "])


def test_a_leading_double_dash_separator_is_dropped_and_later_ones_are_forwarded() -> None:
    command = ["/opt/hunter/bin/opencode", "run", "--model", "p/m", "--", "--model=x", "--", "tail"]
    plain = lifecycle.parse_arguments([*EXECUTE_HEAD, "--model-argv", *command])
    separated = lifecycle.parse_arguments([*EXECUTE_HEAD, "--model-argv", "--", *command])
    assert plain.model_argv == separated.model_argv == tuple(command)


def test_the_first_model_argv_is_the_boundary_and_later_ones_belong_to_the_command() -> None:
    arguments = lifecycle.parse_arguments(
        [*EXECUTE_HEAD, "--model-argv", "opencode", "--model-argv", "again", "--help"]
    )
    assert arguments.model_argv == ("opencode", "--model-argv", "again", "--help")  # even --help is not ours here


def test_model_argv_is_valid_for_execute_only() -> None:
    with pytest.raises(SystemExit):
        lifecycle.parse_arguments(["bound", "--issue", "1", "--authorization-id", "a", "--model-argv", "x"])


def test_the_model_command_reaches_the_executor_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_executor(**kwargs: Any) -> Any:
        seen.update(kwargs)
        raise RuntimeError("stop after the model command is captured")

    monkeypatch.setattr(lifecycle.roles, "run_executor", fake_executor)
    monkeypatch.setattr(lifecycle.roles, "SudoIsolation", lambda _user: object())
    monkeypatch.setattr(lifecycle, "_run_context", lambda: (1, 1, "c" * 40))
    monkeypatch.setattr(lifecycle, "_secret", lambda name: "secret-value")
    monkeypatch.setattr(lifecycle, "_ledger_access", lambda _c: object())
    monkeypatch.setattr(lifecycle, "_x25519", lambda _n: object())
    monkeypatch.setattr(lifecycle, "_remote", lambda _c: "remote")
    monkeypatch.setattr(lifecycle.Path, "read_bytes", lambda self: b"handoff")
    argv = _execute_argv()
    arguments = lifecycle.parse_arguments(argv)
    key = Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key()
    public_hex = key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", "unset")  # restored on teardown; cmd_execute writes it
    configuration = type("C", (), {"prompt_verifying_key": public_hex, "result_recipient": object()})()
    with pytest.raises(RuntimeError, match="model command is captured"):
        lifecycle.cmd_execute(configuration, arguments)  # type: ignore[arg-type]
    assert seen["config"].model_argv == tuple(argv[argv.index("--model-argv") + 1 :])


def test_the_candidate_pr_opener_invocation_matches_its_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = dict(_runs("hunter-issue-agent-candidate-pr.yml"))
    (run,) = (v for k, v in runs.items() if k.endswith("Open the Draft PR when the candidate is eligible"))
    argv = _argv(re.search(r"hunter_issue_agent_candidate_pr\.py\s+((?:[^\n\\]|\\\n)+)", run).group(1))  # type: ignore[union-attr]
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> tuple[int, Any]:
        seen.update(kwargs)
        return 0, candidate_pr.CandidatePrDecision(False, "ok", None, "", "")  # type: ignore[call-arg]

    monkeypatch.setattr(candidate_pr, "run", fake_run)
    assert candidate_pr.main(argv) == 0
    assert set(seen) >= {"repository", "head_repository", "branch", "head_sha"}
