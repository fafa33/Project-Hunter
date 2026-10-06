"""S6 entrypoint: ``source-handling-bootstrap`` end to end against a real anchored ledger.

The owner-dispatched workflow runs exactly one command, and no other suite executes it. These tests
drive the real entry point (parser, pinned configuration, anchor read, store, canonical bootstrap,
compare-and-swap publish, replay and completeness check) against a local bare remote and an
authenticated GitHub read double, and prove that every adversarial outcome is a bounded refusal that
writes nothing out of scope.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import bootstrap_source_handling_authority as bootstrap
import hunter_issue_agent_lifecycle as lifecycle
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_source_handling_store as sh
from hunter.automation import issue_agent_state as state
from hunter.evidence_intelligence import source_handling_provenance

REPOSITORY = "fafa33/Project-Hunter"
REPOSITORY_ID = 1292945327
OWNER_LOGIN = "fafa33"
RULESET_ID = 24526712
UPDATED_AT = "2026-10-05T19:29:49.614Z"
HEAD_SHA = "c" * 40
RUN_ID = 9001
COMMAND = "source-handling-bootstrap"
#: The trust-root fields ``_export_public_trust`` re-exports; pre-set so ``monkeypatch`` restores them.
EXPORTED_TRUST = (
    "HUNTER_ISSUE_AGENT_REPOSITORY",
    "HUNTER_ISSUE_AGENT_OWNER_LOGIN",
    "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY",
    "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY",
    "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY",
    "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256",
    "HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256",
)


class GitHubDouble:
    """The three authenticated reads ``require_anchor`` and ``run_provenance`` observe."""

    def __init__(self) -> None:
        self.ruleset: Any = {
            "id": RULESET_ID,
            "enforcement": "active",
            "updated_at": UPDATED_AT,
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        }
        self.rules: list[dict[str, Any]] = [
            {"type": "deletion", "ruleset_id": RULESET_ID},
            {"type": "non_fast_forward", "ruleset_id": RULESET_ID},
        ]
        self.run: Any = {
            "id": RUN_ID,
            "run_attempt": 1,
            "path": control.SOURCE_HANDLING_BOOTSTRAP_WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": HEAD_SHA,
            "repository": {"id": REPOSITORY_ID},
            "head_repository": {"id": REPOSITORY_ID},
        }

    def get(self, path: str) -> control.Read:
        if "/rulesets/" in path:
            return control.Read("ok", self.ruleset)
        if "/rules/branches/" in path:
            return control.Read("ok", self.rules)
        if path.endswith("/attempts/1"):
            return control.Read("ok", self.run)
        return control.Read("absent")


def head_of(remote: Path, ref: str) -> str | None:
    listed = subprocess.run(
        ["git", "ls-remote", str(remote), ref], capture_output=True, text=True, check=True
    ).stdout.split()
    return listed[0] if listed else None


def refs_of(remote: Path) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in subprocess.run(
        ["git", "ls-remote", str(remote)], capture_output=True, text=True, check=True
    ).stdout.splitlines():
        sha, ref = line.split("\t")
        refs[ref] = sha
    return refs


def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A bare remote, a provisioned checkout and the environment the workflow really grants."""

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    state_key = Ed25519PrivateKey.generate()
    signing_key = Ed25519PrivateKey.generate()
    sh_material = signing_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    rule = bootstrap._load_production_rule()
    verification_hex, verification_sha256, genesis_sha256 = bootstrap._derived_digests(sh_material, rule)
    checkout = tmp_path / "checkout"
    config = checkout / "config"
    config.mkdir(parents=True)
    trust_roots = {
        "schema_version": "hunter-issue-agent-trust-roots-v1",
        "provisioned": True,
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "owner_login": OWNER_LOGIN,
        "state_keys": [
            state_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        ],
        "anchor": {"ruleset_id": RULESET_ID, "updated_at": UPDATED_AT},
        "handoff_recipient": "ca03f5c9d997b8b829a8f2692c38a7a6cbb189923c3c058726a258986f042d74",
        "result_recipient": "2c1f6fa40c7390d6303b0b5fec3b0a7843a5a029f3490707ade5bdf56ff38756",
        "writer": {"login": OWNER_LOGIN, "name": "Farhad5778", "email": "34549283+fafa33@users.noreply.github.com"},
        "authorization_verifying_key": "817c9612d91ef2f0f391c997e6243e2afb346eb0b03c3d4b68aac43a855936c0",
        "prompt_verifying_key": "b56a357abed658aad18c63f9c35c72316949474bffd31a078df8d41a1f447bfd",
        "source_handling": {
            "verification_key": verification_hex,
            "verification_key_sha256": verification_sha256,
            "genesis_rule_sha256": genesis_sha256,
        },
    }
    (config / "issue_agent_trust_roots.json").write_text(json.dumps(trust_roots), encoding="utf-8")
    environment = {
        "GITHUB_TOKEN": "ghs_" + "d" * 20,
        "GITHUB_RUN_ID": str(RUN_ID),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": HEAD_SHA,
        lifecycle.STATE_SIGNING_KEY_ENV: state_key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        ).hex(),
        bootstrap.SIGNING_KEY_ENV: sh_material.hex(),
        "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY": "11" * 32,
    }
    environment.update({name: "" for name in EXPORTED_TRUST})
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(source_handling_provenance, "_production_view", None)
    github = GitHubDouble()
    monkeypatch.setattr(lifecycle, "_remote", lambda _configuration: str(remote))
    monkeypatch.setattr(lifecycle, "_github", lambda _configuration: github)
    return {
        "remote": remote,
        "checkout": checkout,
        "trust_roots": trust_roots,
        "github": github,
        "state_key": state_key,
        "signing_key_hex": sh_material.hex(),
    }


def amend_trust_roots(world: dict[str, Any], **changes: Any) -> None:
    document = dict(world["trust_roots"])
    document.update(changes)
    path = world["checkout"] / "config" / "issue_agent_trust_roots.json"
    path.write_text(json.dumps(document), encoding="utf-8")


def dispatch(world: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = lifecycle.main(["--checkout", str(world["checkout"]), COMMAND])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ledger_files(world: dict[str, Any]) -> tuple[str | None, list[tuple[str, dict[str, bytes]]]]:
    store = state.GitLedgerStore(str(world["remote"]))
    return store.read_files(sh.SOURCE_HANDLING_LEDGER_REF, frozenset({"record.json", "delta.json"}))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return build(tmp_path, monkeypatch)


def test_a_valid_first_bootstrap_publishes_and_verifies_the_anchored_ledger(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = dispatch(world, capsys)
    assert code == 0, err
    assert "source-handling bootstrap: complete and verified at " in out
    head, entries = ledger_files(world)
    assert head is not None and head == head_of(world["remote"], sh.SOURCE_HANDLING_LEDGER_REF)
    assert [sorted(files) for _, files in entries] == [["delta.json", "record.json"]] * len(entries)
    record = json.loads(entries[0][1]["record.json"])
    assert record["recorded_by"] == {
        "workflow_path": control.SOURCE_HANDLING_BOOTSTRAP_WORKFLOW,
        "job": COMMAND,
        "role": COMMAND,
        "run_id": RUN_ID,
        "run_attempt": 1,
        "head_sha": HEAD_SHA,
    }
    assert record["repository_id"] == REPOSITORY_ID
    assert record["record_seq"] == 0 and record["prev_record_sha256"] is None
    assert record["signature"]["domain"] == sh.SOURCE_HANDLING_LEDGER_DOMAIN


def test_a_second_dispatch_is_idempotent_and_writes_nothing(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert dispatch(world, capsys)[0] == 0
    before = refs_of(world["remote"])
    code, out, err = dispatch(world, capsys)
    assert code == 0, err
    assert "source-handling bootstrap: complete and verified at " in out
    assert refs_of(world["remote"]) == before


def test_a_malformed_pre_existing_ledger_is_refused_as_state_corrupt(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    store = state.GitLedgerStore(str(world["remote"]))
    store.append_files(
        sh.SOURCE_HANDLING_LEDGER_REF,
        None,
        {"record.json": b"{}", "delta.json": b""},
        message="not a ledger record",
        timestamp="2026-10-06T00:00:00Z",
    )
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "STATE_CORRUPT" in err and "Traceback" not in err
    assert len(refs_of(world["remote"])) == 1


def test_a_target_ref_outside_the_state_namespace_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sh, "SOURCE_HANDLING_LEDGER_REF", "refs/heads/main")
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "ANCHOR_INTEGRITY_FAILED" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_ledger_binding_a_foreign_repository_is_refused(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert dispatch(world, capsys)[0] == 0
    amend_trust_roots(world, repository_id=999999999)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "STATE_CORRUPT" in err and "foreign repository" in err and "Traceback" not in err


def test_an_unprovisioned_trust_roots_document_refuses_before_any_read_or_write(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    amend_trust_roots(world, provisioned=False)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unprovisioned checkout must not reach the store, GitHub or a subprocess")

    monkeypatch.setattr(lifecycle, "_remote", forbidden)
    monkeypatch.setattr(lifecycle, "_github", forbidden)
    monkeypatch.setattr(lifecycle, "_secret", forbidden)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "MISSING_CONFIGURATION" in err
    assert refs_of(world["remote"]) == {}


def test_a_weakened_anchor_is_frozen_before_the_ledger_is_read(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    world["github"].ruleset["updated_at"] = "2026-10-06T00:00:00.000Z"
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "ANCHOR_INTEGRITY_FAILED" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_missing_anchor_ruleset_is_frozen(world: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    world["github"].ruleset = None
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "ANCHOR_INTEGRITY_FAILED" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_genesis_that_does_not_match_the_pinned_trust_roots_is_blocked(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    amend_trust_roots(
        world, source_handling={**world["trust_roots"]["source_handling"], "genesis_rule_sha256": "42" * 32}
    )
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "SOURCE_HANDLING_BLOCKED" in err and "genesis" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_self_inconsistent_verification_key_in_the_trust_roots_is_refused(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    amend_trust_roots(world, source_handling={**world["trust_roots"]["source_handling"], "verification_key": "42" * 32})
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "MISSING_CONFIGURATION" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_signing_key_that_yields_another_verification_key_is_blocked(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(bootstrap.SIGNING_KEY_ENV, "43" * 32)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "SOURCE_HANDLING_BLOCKED" in err and "pinned trust roots" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_tampered_production_authorization_rule_is_blocked(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tampered = world["checkout"] / "authorization_rule_v1.json"
    tampered.write_text(json.dumps({"authorization_rule_id": "forged"}), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_DEFAULT_RULE", tampered)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "SOURCE_HANDLING_BLOCKED" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_missing_state_signing_key_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(lifecycle.STATE_SIGNING_KEY_ENV)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "MISSING_CONFIGURATION" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_malformed_source_handling_signing_key_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(bootstrap.SIGNING_KEY_ENV, "not-hex-at-all")
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "MISSING_CONFIGURATION" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_state_signing_key_that_is_not_pinned_in_the_trust_roots_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(lifecycle.STATE_SIGNING_KEY_ENV, "42" * 32)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "STATE_CORRUPT" in err and "Traceback" not in err


def test_a_workflow_re_run_is_refused_before_it_can_write(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "RERUN_REFUSED" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_a_lost_compare_and_swap_is_a_bounded_refusal(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_publish = sh.publish

    def racing_publish(*args: Any, **kwargs: Any) -> Any:
        state.GitLedgerStore(str(world["remote"])).append_files(
            sh.SOURCE_HANDLING_LEDGER_REF,
            head_of(world["remote"], sh.SOURCE_HANDLING_LEDGER_REF),
            {"record.json": b"{}", "delta.json": b""},
            message="another writer won the lease",
            timestamp="2026-10-06T00:00:00Z",
        )
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(sh, "publish", racing_publish)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "re-dispatch the bootstrap" in err and "Traceback" not in err
    assert len(refs_of(world["remote"])) == 1


def test_a_failure_before_any_durable_write_leaves_the_ref_absent(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse_publish(*_args: Any, **_kwargs: Any) -> Any:
        raise state.LedgerError("simulated failure before any durable write")

    monkeypatch.setattr(sh, "publish", refuse_publish)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "re-dispatch the bootstrap" in err and "Traceback" not in err
    assert refs_of(world["remote"]) == {}


def test_an_interrupted_prefix_is_recovered_by_the_next_dispatch(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_publish = sh.publish
    armed = {"value": True}

    def interrupted_publish(store: Any, position: Any, transactions: Any, **kwargs: Any) -> Any:
        if armed["value"] and transactions:
            armed["value"] = False
            real_publish(store, position, transactions[:1], **kwargs)
            raise state.LedgerConflictError("simulated crash after the first durable write")
        return real_publish(store, position, transactions, **kwargs)

    monkeypatch.setattr(sh, "publish", interrupted_publish)
    code, out, err = dispatch(world, capsys)
    assert code == lifecycle.EXIT_REFUSED
    assert "re-dispatch the bootstrap" in err and "Traceback" not in err
    assert head_of(world["remote"], sh.SOURCE_HANDLING_LEDGER_REF) is not None
    _, first_run = ledger_files(world)
    assert len(first_run) == 1

    code, out, err = dispatch(world, capsys)
    assert code == 0, err
    head, second_run = ledger_files(world)
    assert head == head_of(world["remote"], sh.SOURCE_HANDLING_LEDGER_REF)
    assert len(second_run) == 2
    record = json.loads(second_run[1][1]["record.json"])
    assert record["record_seq"] == 1
    assert record["recorded_by"]["run_id"] == RUN_ID


def test_the_bootstrap_writes_only_the_anchored_source_handling_ref(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert dispatch(world, capsys)[0] == 0
    assert refs_of(world["remote"]) == {
        sh.SOURCE_HANDLING_LEDGER_REF: head_of(world["remote"], sh.SOURCE_HANDLING_LEDGER_REF)
    }


def test_no_key_or_token_reaches_stdout_or_stderr(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_key_hex = (
        world["state_key"]
        .private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        .hex()
    )
    token = "ghs_" + "d" * 20
    code, out, err = dispatch(world, capsys)
    assert code == 0, err
    monkeypatch.setenv(bootstrap.SIGNING_KEY_ENV, "not-hex-at-all")
    refused, refused_out, refused_err = dispatch(world, capsys)
    assert refused == lifecycle.EXIT_REFUSED
    for captured in (out, err, refused_out, refused_err):
        assert state_key_hex not in captured
        assert world["signing_key_hex"] not in captured
        assert token not in captured
        assert "Traceback" not in captured
