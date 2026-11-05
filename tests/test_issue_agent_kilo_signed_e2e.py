"""Signed-ledger real Kilo Docker execution -> sealed result -> independent validator.

The local safety port is deterministic and credential-free; hosted production
pre-push safety must still be proven in its own trusted CI environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
import test_issue_agent_roles as fixture

from hunter.automation import issue_agent_kilo_container as kilo_port
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
from hunter.automation.issue_agent_transport import header

repos = fixture.repos
_prompt_keys = fixture._prompt_keys


@pytest.mark.skipif(os.environ.get("HUNTER_RUN_REAL_KILO_E2E") != "1", reason="requires Docker and live Kilo model")
@pytest.mark.usefixtures("_prompt_keys")
def test_signed_authorization_real_kilo_sealed_result_independent_validator(
    repos: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = (
        "In /workspace/test_sample.py there is a deliberately broken assertion. "
        "First use the bash tool to run exactly `python -m pytest -q test_sample.py` and observe failure. "
        "Then use the edit tool to change only the incorrect expected number from 5 to 4 in that file. "
        "Then use the bash tool to run exactly `python -m pytest -q test_sample.py` again. "
        "Do not use other bash commands. Report both test results."
    )
    monkeypatch.setattr(fixture, "PROMPT", prompt)
    seed = repos["tmp"] / "seed"
    (seed / "test_sample.py").write_text("def test_sample():\n    assert 2 + 2 == 5\n")
    fixture.git(seed, "add", "test_sample.py")
    fixture.git(
        seed, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "seed"
    )
    fixture.git(seed, "push", "-q", repos["remote"], "HEAD:refs/heads/main")
    repos["base"] = fixture.git(seed, "rev-parse", "HEAD")
    fixture.git(repos["trusted"], "pull", "-q", "--ff-only")
    ledger, handoff = fixture.authorize(repos, allowed=("test_sample.py",), sealed_prompt=prompt)
    base_config = fixture.executor_config(repos)
    config = roles.ExecutorConfig(
        remote=base_config.remote,
        model_argv=("kilo", "run", "--model", "kilo/kilo-auto/free"),
        model_public_env=base_config.model_public_env,
        model_secret_env={},
        handoff_key=base_config.handoff_key,
        result_recipient=base_config.result_recipient,
        prompt_verifier=base_config.prompt_verifier,
        model_timeout_seconds=150,
    )
    isolation = KiloContainerIsolation(Path(__file__).resolve().parents[1] / "scripts/issue_agent")
    started = time.monotonic()
    outcome = roles.run_executor(
        issue=fixture.ISSUE,
        authorization_id=fixture.AUTH,
        context=roles.RoleContext(100, 1),
        ledger=ledger.access,
        handoff_envelope=handoff,
        config=config,
        isolation=isolation,
        workroot=repos["tmp"] / "signed-kilo-execute",
    )
    elapsed = time.monotonic() - started
    assert outcome.sealed_result is not None, outcome.advisory_code
    # Acceptance evidence from what the broker actually executed (not from the prompt): a failing test,
    # a workspace change, then a passing retest, all inside the explicit attempt and wall-clock budgets.
    trace = isolation.trace
    assert len(trace) >= 2, trace
    assert all(entry["cmd"] == "python -m pytest -q test_sample.py" for entry in trace)
    assert trace[0]["exit"] != 0 and trace[0]["stdout_bytes"] > 0  # failure with diagnostic output returned to Kilo
    assert trace[-1]["exit"] == 0
    assert trace[0]["tree"] != trace[-1]["tree"]  # the workspace was edited between the failing and passing runs
    assert len(trace) <= kilo_port.BROKER_MAX_COMMANDS
    assert elapsed < kilo_port.BROKER_DEADLINE_SECONDS
    ledger.write(
        state.RESULT_BOUND,
        {
            "result_artifact": fixture._artifact(outcome.sealed_result, 22),
            "result_plaintext_sha256": header(outcome.sealed_result).plaintext_sha256,
            "executor_job_id": 5,
            "executor_conclusion": "success",
            "executor_advisory_code": None,
        },
        "bind",
    )
    receipt = fixture.validate(repos, ledger, outcome.sealed_result)
    assert receipt["verdict"] == "PASS"
    assert receipt["execution_id"] == state.execution_identity(
        authorization_id=fixture.AUTH,
        authorize_run_id=100,
        control_sha=fixture.CONTROL,
        handoff_sha256=state.sha256_hex(fixture.handoff_bundle(prompt)),
    )
    assert receipt["result_sha256"] == header(outcome.sealed_result).plaintext_sha256
    assert receipt["ciphertext_sha256"] == state.sha256_hex(outcome.sealed_result)
    assert receipt["base_sha"] == repos["base"]
    event = {}
    if os.environ.get("GITHUB_EVENT_PATH"):
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pull_request = event.get("pull_request", {})
    proof = {
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "pull_request": pull_request.get("number"),
        "head_sha": pull_request.get("head", {}).get("sha"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job": os.environ.get("GITHUB_JOB"),
        "execution_id": receipt["execution_id"],
        "validator_verdict": receipt["verdict"],
        "result_sha256": receipt["result_sha256"],
        "ciphertext_sha256": receipt["ciphertext_sha256"],
        "broker_trace": [[e["seq"], e["exit"], str(e["tree"])[:12]] for e in trace],
        "attempts_used": len(trace),
        "attempt_budget": kilo_port.BROKER_MAX_COMMANDS,
        "elapsed_seconds": round(elapsed, 1),
        "deadline_seconds": kilo_port.BROKER_DEADLINE_SECONDS,
    }
    if os.environ.get("GITHUB_ACTIONS") == "true":
        assert proof["pull_request"] and proof["head_sha"] and proof["run_id"] and proof["job"], proof
    proof["proof_sha256"] = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    print("HUNTER_682_ACCEPTANCE_PROOF " + json.dumps(proof, sort_keys=True))
    print("SIGNED_KILO_RUNNER_TO_VALIDATOR_PASS", receipt["execution_id"], receipt["unsigned_commit_sha"])
    corrupted = bytearray(outcome.sealed_result)
    corrupted[-1] ^= 1
    with pytest.raises(roles.RoleRefused):
        fixture.validate(repos, ledger, bytes(corrupted))
