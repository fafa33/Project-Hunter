"""Signed-ledger real Kilo Docker execution -> sealed result -> independent validator.

The local safety port is deterministic and credential-free; hosted production
pre-push safety must still be proven in its own trusted CI environment.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import test_issue_agent_roles as fixture

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
    assert outcome.sealed_result is not None, outcome.advisory_code
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
    print("SIGNED_KILO_RUNNER_TO_VALIDATOR_PASS", receipt["execution_id"], receipt["unsigned_commit_sha"])
    corrupted = bytearray(outcome.sealed_result)
    corrupted[-1] ^= 1
    with pytest.raises(roles.RoleRefused):
        fixture.validate(repos, ledger, bytes(corrupted))
