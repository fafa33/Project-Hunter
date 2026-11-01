from datetime import UTC, datetime, timedelta

import pytest

from hunter.market_validation.models import ProjectValidationTarget
from hunter.market_validation.universe_membership import (
    MarketValidationUniverseMembershipAuthority,
    MembershipEvidence,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def evidence(i, *, entity=None, known_at=NOW, identity="exact", lifecycle="queued"):
    entity = entity or f"entity-{i}"
    return MembershipEvidence(
        candidate_id=f"candidate-{i}",
        canonical_entity_id=entity,
        project=ProjectValidationTarget(entity, entity, "other"),
        identity_outcome=identity,
        lifecycle_status=lifecycle,
        known_at=known_at,
        observed_at=NOW,
        source_digest=f"sha256:{i}",
        registry_reference=f"registry:{i}",
        provenance_verified=True,
    )


def authorize(items):
    return MarketValidationUniverseMembershipAuthority().authorize(
        tuple(items),
        cutoff=NOW,
        run_id="candidate-run",
        baseline_run_id="baseline",
        policy_version="v1",
        output_directory="reports/candidate",
        baseline_output_directory="reports/baseline",
    )


def test_fifty_distinct_entities_are_runnable_and_permutation_invariant():
    items = [evidence(i) for i in range(50)]
    a, b = authorize(items), authorize(reversed(items))
    assert a.runnable and len(a.targets) == 50
    assert a.manifest_digest == b.manifest_digest
    assert a.decisions == b.decisions


def test_cross_chain_duplicate_cannot_inflate_minimum():
    items = [evidence(i) for i in range(49)] + [evidence(49, entity="entity-0")]
    result = authorize(items)
    assert not result.runnable
    assert len(result.targets) == 49
    assert sum(d.reason == "duplicate economic entity" for d in result.decisions) == 1


def test_late_or_untrusted_identity_fails_closed():
    items = [evidence(1, known_at=NOW + timedelta(seconds=1)), evidence(2, identity="probable")]
    result = authorize(items)
    assert not result.runnable
    assert [d.outcome for d in result.decisions] == ["unavailable", "not_included"]


def test_rejects_baseline_run_and_minimum_weakening():
    authority = MarketValidationUniverseMembershipAuthority()
    with pytest.raises(ValueError):
        authority.authorize(
            (),
            cutoff=NOW,
            run_id="baseline",
            baseline_run_id="baseline",
            policy_version="v1",
            output_directory="reports/candidate",
            baseline_output_directory="reports/baseline",
        )
    with pytest.raises(ValueError):
        authority.authorize(
            (),
            cutoff=NOW,
            run_id="candidate",
            baseline_run_id="baseline",
            policy_version="v1",
            minimum_entities=1,
            output_directory="reports/candidate",
            baseline_output_directory="reports/baseline",
        )


def test_unproven_mapping_fails_closed():
    item = evidence(1)
    from dataclasses import replace

    result = authorize([replace(item, canonical_entity_id="other-entity")])
    assert result.decisions[0].outcome == "unavailable"


def test_unverified_provenance_and_overlapping_outputs_fail_closed():
    from dataclasses import replace

    result = authorize([replace(evidence(1), provenance_verified=False)])
    assert result.decisions[0].outcome == "unavailable"
    with pytest.raises(ValueError):
        MarketValidationUniverseMembershipAuthority().authorize(
            (),
            cutoff=NOW,
            run_id="candidate",
            baseline_run_id="baseline",
            policy_version="v1",
            output_directory="reports/baseline/candidate",
            baseline_output_directory="reports/baseline",
        )


def test_dotdot_alias_cannot_escape_output_isolation():
    with pytest.raises(ValueError, match="noncanonical"):
        MarketValidationUniverseMembershipAuthority().authorize(
            tuple(evidence(i) for i in range(50)),
            cutoff=NOW,
            run_id="candidate",
            baseline_run_id="baseline",
            policy_version="v1",
            output_directory="reports/candidate/../baseline",
            baseline_output_directory="reports/baseline",
        )


def test_authorized_target_sector_and_metadata_bind_manifest_digest():
    from dataclasses import replace

    items = [evidence(i) for i in range(50)]
    original = authorize(items)
    changed_sector = list(items)
    changed_sector[0] = replace(
        items[0], project=replace(items[0].project, sector="defi", metadata=dict(items[0].project.metadata))
    )
    changed_metadata = list(items)
    changed_metadata[0] = replace(items[0], project=replace(items[0].project, metadata={"chain": "ethereum"}))
    assert original.runnable
    assert authorize(changed_sector).manifest_digest != original.manifest_digest
    assert authorize(changed_metadata).manifest_digest != original.manifest_digest
