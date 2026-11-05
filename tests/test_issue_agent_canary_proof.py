"""The acceptance proof must bind to GitHub's authoritative job identity and fail closed otherwise (Issue #682)."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_canary_proof", Path(__file__).resolve().parents[1] / "scripts/issue_agent/verify_canary_proof.py"
)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

HEAD = "ab" * 20
BASE = {
    "repository": "o/r",
    "pull_request": 684,
    "head_sha": HEAD,
    "run_id": "99",
    "run_attempt": "1",
    "job": "signed-kilo-canary",
    "validator_verdict": "PASS",
    "execution_id": "e" * 64,
}
ENV = {"GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "99", "GITHUB_RUN_ATTEMPT": "1"}
EVENT = {"pull_request": {"number": 684, "head": {"sha": HEAD}}}
RUN = {
    "id": 99,
    "run_attempt": 1,
    "head_sha": HEAD,
    "repository": {"full_name": "o/r"},
    "pull_requests": [{"number": 684}],
}
JOBS = {
    "jobs": [
        {
            "id": 12345,
            "name": "signed-kilo-canary",
            "run_id": 99,
            "run_attempt": 1,
            "head_sha": HEAD,
            "status": "in_progress",
            "html_url": "u",
        }
    ]
}


def sealed(proof: dict) -> dict:
    out = {k: v for k, v in proof.items() if k != "proof_sha256"}
    out["proof_sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return out


def run(proof=None, run_=None, jobs=None, event=None, env=None):
    return verify.canonicalize(
        sealed(proof or BASE), run=run_ or RUN, jobs=jobs or JOBS, event=event or EVENT, env=env or ENV
    )


def test_valid_proof_is_bound_to_the_numeric_job_id() -> None:
    record = run()
    assert record["job_id"] == 12345 and len(record["canonical_proof_sha256"]) == 64
    assert record["acceptance_proof_sha256"] == sealed(BASE)["proof_sha256"]


def mutated(**changes):
    return {**BASE, **changes}


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"proof": mutated(head_sha="cd" * 20)}, "head SHA"),
        ({"proof": mutated(run_id="100")}, "run id"),
        ({"proof": mutated(run_attempt="2")}, "attempt"),
        ({"proof": mutated(repository="evil/r")}, "repository"),
        ({"proof": mutated(pull_request=1)}, "pull request"),
        ({"proof": mutated(validator_verdict="FAIL")}, "validator"),
        ({"proof": mutated(job="other")}, "unique"),
        ({"event": {"pull_request": {"number": 684, "head": {"sha": "short"}}}}, "exact PR head"),
        ({"run_": {**RUN, "pull_requests": []}}, "not bound"),
        ({"run_": {**RUN, "head_sha": "cd" * 20}}, "head SHA"),
        ({"env": {**ENV, "GITHUB_RUN_ATTEMPT": "2"}}, "attempt"),
    ],
)
def test_mismatched_or_stale_facts_are_rejected(kwargs, reason) -> None:
    with pytest.raises(verify.ProofRejected, match=reason):
        run(**kwargs)


def test_duplicate_wrong_run_wrong_head_or_finished_jobs_are_rejected() -> None:
    job = JOBS["jobs"][0]
    for jobs, reason in (
        ({"jobs": [job, copy.deepcopy(job)]}, "unique"),
        ({"jobs": [{**job, "run_attempt": 2}]}, "another run"),
        ({"jobs": [{**job, "run_id": 5}]}, "another run"),
        ({"jobs": [{**job, "head_sha": "cd" * 20}]}, "head SHA"),
        ({"jobs": [{**job, "status": "completed"}]}, "currently executing"),
        ({"jobs": [{**job, "id": "12345"}]}, "numeric id"),
        ({"jobs": []}, "unique"),
    ):
        with pytest.raises(verify.ProofRejected, match=reason):
            run(jobs=jobs)


def test_forged_or_duplicated_proof_records_in_the_log_are_rejected() -> None:
    good = verify.PROOF_PREFIX + json.dumps(sealed(BASE))
    assert verify.extract_proof("noise\n" + good + "\n")["job"] == "signed-kilo-canary"
    tampered = json.loads(good.removeprefix(verify.PROOF_PREFIX))
    tampered["job"] = "x"
    for log in ("no proof here", good + "\n" + good, verify.PROOF_PREFIX + json.dumps(tampered)):
        with pytest.raises(verify.ProofRejected):
            verify.extract_proof(log)


def test_main_fails_closed_without_a_token_and_on_api_errors(tmp_path, monkeypatch) -> None:
    log = verify.PROOF_PREFIX + json.dumps(sealed(BASE))
    event = tmp_path / "event.json"
    event.write_text(json.dumps(EVENT))
    for name, value in {**ENV, "GITHUB_EVENT_PATH": str(event)}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert verify.main(io.StringIO(log)) == 1  # missing token
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def api(url: str, token: str):
        return RUN if "jobs" not in url else JOBS

    assert verify.main(io.StringIO(log), fetch=api) == 0

    def broken(url: str, token: str):
        raise OSError("unavailable")

    assert verify.main(io.StringIO(log), fetch=broken) == 1


def test_no_file_path_is_taken_from_the_command_line() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts/issue_agent/verify_canary_proof.py").read_text()
    assert "sys.argv" not in source and "argv[" not in source
