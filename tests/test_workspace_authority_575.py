"""Issue #575: fail-closed workspace authority foundation regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.automation.workspace_authority import (
    WorkspaceAuthorityMismatch,
    WorkspaceGrant,
    WorkspaceRegistry,
)

SHA = "a" * 40


def grant(tmp_path: Path) -> tuple[WorkspaceGrant, str]:
    token = "secret-fence"
    return (
        WorkspaceGrant(
            repository="fafa33/Project-Hunter",
            issue=575,
            task_scope_digest="b" * 64,
            workspace=str(tmp_path / "registered"),
            branch="issue-575-global-workspace-authority",
            base_sha=SHA,
            head_sha=SHA,
            checkpoint="checkpoint-575-1",
            lease_owner="writer-1",
            lease_token_digest=hashlib.sha256(token.encode()).hexdigest(),
            lease_expiry=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            fencing=1,
            next_action="validate",
        ),
        token,
    )


def admit(registry: WorkspaceRegistry, g: WorkspaceGrant, token: str, **overrides: object) -> WorkspaceGrant:
    args = dict(
        issue=g.issue,
        repository=g.repository,
        workspace=Path(g.workspace),
        branch=g.branch,
        head_sha=g.head_sha,
        base_sha=g.base_sha,
        checkpoint=g.checkpoint,
        lease_token=token,
    )
    args.update(overrides)
    return registry.admit(**args)


def test_registered_exact_lineage_admits(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    assert registry.write_cas(expected_revision=None, grants={"575": g}) == 1
    assert admit(registry, g, token) == g
    assert registry.write_cas(expected_revision=1, grants={"575": g}) == 2
    with pytest.raises(WorkspaceAuthorityMismatch, match="stale registry revision"):
        registry.write_cas(expected_revision=1, grants={"575": g})


@pytest.mark.parametrize(
    "override",
    [
        {"issue": 576},
        {"repository": "other/repo"},
        {"workspace": Path("/tmp/unregistered")},
        {"branch": "main"},
        {"head_sha": "c" * 40},
        {"checkpoint": ""},
        {"lease_token": "wrong"},
    ],
)
def test_mismatched_coordinates_refuse(tmp_path: Path, override: dict[str, object]) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    registry.write_cas(expected_revision=None, grants={"575": g})
    with pytest.raises(WorkspaceAuthorityMismatch, match="WORKSPACE_AUTHORITY_MISMATCH"):
        admit(registry, g, token, **override)


def test_missing_and_tampered_registry_refuse(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    with pytest.raises(WorkspaceAuthorityMismatch, match="registry unavailable"):
        admit(registry, g, token)
    registry.write_cas(expected_revision=None, grants={"575": g})
    raw = json.loads(registry.path.read_text())
    raw["grants"]["575"]["head_sha"] = "c" * 40
    registry.path.write_text(json.dumps(raw))
    with pytest.raises(WorkspaceAuthorityMismatch, match="registry integrity"):
        admit(registry, g, token)


def test_expired_lease_and_missing_checkpoint_refuse(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    expired = replace(g, lease_expiry=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    registry.write_cas(expected_revision=None, grants={"575": expired})
    with pytest.raises(WorkspaceAuthorityMismatch, match="lease expired"):
        admit(registry, expired, token)
    with pytest.raises(WorkspaceAuthorityMismatch, match="checkpoint"):
        registry.write_cas(expected_revision=1, grants={"575": replace(g, checkpoint="")})


def test_duplicate_workspace_refused(tmp_path: Path) -> None:
    g, _ = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace ownership conflict"):
        registry.write_cas(expected_revision=None, grants={"575": g, "576": replace(g, issue=576)})


def test_canonical_alias_workspace_ownership_refused(tmp_path: Path) -> None:
    g, _ = grant(tmp_path)
    alias = str(tmp_path / "alias" / ".." / "registered")
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace ownership conflict"):
        registry.write_cas(
            expected_revision=None,
            grants={"575": g, "576": replace(g, issue=576, workspace=alias)},
        )


def test_signed_base_must_match_registered_base_even_when_head_matches(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    registry.write_cas(expected_revision=None, grants={"575": replace(g, base_sha="c" * 40)})
    with pytest.raises(WorkspaceAuthorityMismatch, match="workspace lineage differs"):
        admit(registry, g, token)


def test_forged_self_digest_cannot_bypass_authority(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    registry.write_cas(expected_revision=None, grants={"575": g})
    raw = json.loads(registry.path.read_text())
    raw["grants"]["575"]["head_sha"] = "c" * 40
    body = {k: v for k, v in raw.items() if k != "digest"}
    raw["digest"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    registry.path.write_text(json.dumps(raw))
    with pytest.raises(WorkspaceAuthorityMismatch, match="registry integrity"):
        admit(registry, g, token)


def test_missing_or_wrong_trusted_key_fails_closed(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    path = tmp_path / "authority.json"
    WorkspaceRegistry(path, authority_key=b"a" * 32).write_cas(expected_revision=None, grants={"575": g})
    for key in (None, b"b" * 32):
        with pytest.raises(WorkspaceAuthorityMismatch):
            admit(WorkspaceRegistry(path, authority_key=key), g, token)


def test_interprocess_cas_only_one_writer_wins(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    g, _ = grant(tmp_path)
    path = tmp_path / "authority.json"
    key = b"a" * 32
    WorkspaceRegistry(path, authority_key=key).write_cas(expected_revision=None, grants={"575": g})

    def write(_: int) -> str:
        try:
            WorkspaceRegistry(path, authority_key=key).write_cas(expected_revision=1, grants={"575": g})
            return "success"
        except WorkspaceAuthorityMismatch:
            return "stale"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write, range(8)))
    assert results.count("success") == 1
    assert results.count("stale") == 7
    assert WorkspaceRegistry(path, authority_key=key).read()["revision"] == 2


def test_publisher_consumes_same_authenticated_grant(tmp_path: Path) -> None:
    g, token = grant(tmp_path)
    registry = WorkspaceRegistry(tmp_path / "authority.json", authority_key=b"a" * 32)
    registry.write_cas(expected_revision=None, grants={"575": g})
    assert (
        registry.admit_publication(
            issue=g.issue,
            repository=g.repository,
            branch=g.branch,
            base_sha=g.base_sha,
            checkpoint=g.checkpoint,
            lease_token=token,
        )
        == g
    )
    for changed in ({"base_sha": "f" * 40}, {"branch": "foreign"}, {"checkpoint": "stale"}):
        kwargs = dict(
            issue=g.issue,
            repository=g.repository,
            branch=g.branch,
            base_sha=g.base_sha,
            checkpoint=g.checkpoint,
            lease_token=token,
        )
        kwargs.update(changed)
        with pytest.raises(WorkspaceAuthorityMismatch, match="publication lineage differs"):
            registry.admit_publication(**kwargs)
    with pytest.raises(WorkspaceAuthorityMismatch, match="publication lease token"):
        registry.admit_publication(
            issue=g.issue,
            repository=g.repository,
            branch=g.branch,
            base_sha=g.base_sha,
            checkpoint=g.checkpoint,
            lease_token="wrong",
        )
