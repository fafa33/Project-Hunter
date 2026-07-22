from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.committee.authority import (
    CommitteeInputIdentity,
    CommitteeInputPolicyError,
    ResolvedCommitteeInput,
    validate_authoritative_input,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
IDENTITY = CommitteeInputIdentity(
    project_id="alpha",
    entity_id="entity:alpha",
    representation_id="ethereum:0xalpha",
    chain_id="eip155:1",
)


@dataclass(frozen=True)
class Input:
    id: str
    effective_at: datetime


def resolved(value: Input, **changes: object) -> ResolvedCommitteeInput:
    base = ResolvedCommitteeInput(
        record_id=value.id,
        family="snapshot",
        value=value,
        authority_class="production-authoritative",
        identity=IDENTITY,
        recorded_at=NOW - timedelta(hours=1),
        effective_at=value.effective_at,
        lineage_id="lineage:alpha",
        revision_id="revision:2",
        current_revision_id="revision:2",
    )
    return replace(base, **changes)


def validate(value: Input, authority: ResolvedCommitteeInput) -> None:
    validate_authoritative_input(
        value,
        authority,
        family="snapshot",
        expected_identity=IDENTITY,
        cycle_effective_at=NOW,
    )


def test_repository_resolved_record_is_required_and_must_match_supplied_value() -> None:
    value = Input("snapshot:alpha", NOW - timedelta(hours=1))
    validate(value, resolved(value))

    forged = replace(value, effective_at=NOW - timedelta(hours=2))
    with pytest.raises(CommitteeInputPolicyError, match="differs from authoritative persisted record"):
        validate(forged, resolved(value))


def test_authority_class_is_explicit_and_fail_closed() -> None:
    value = Input("snapshot:alpha", NOW - timedelta(hours=1))
    with pytest.raises(CommitteeInputPolicyError, match="requires explicit authority class"):
        validate(value, resolved(value, authority_class=""))
    for authority_class in ("descriptive-only", "experimental", "unavailable"):
        with pytest.raises(CommitteeInputPolicyError, match="cannot affect authoritative scoring"):
            validate(value, resolved(value, authority_class=authority_class))
    with pytest.raises(CommitteeInputPolicyError, match="unknown committee input authority class"):
        validate(value, resolved(value, authority_class="legacy"))


def test_full_identity_scope_is_required() -> None:
    value = Input("snapshot:alpha", NOW - timedelta(hours=1))
    mismatched = replace(IDENTITY, representation_id="base:0xalpha", chain_id="eip155:8453")
    with pytest.raises(CommitteeInputPolicyError, match="identity mismatch"):
        validate(value, resolved(value, identity=mismatched))


def test_known_by_hunter_and_effective_cutoffs_are_enforced() -> None:
    value = Input("snapshot:alpha", NOW - timedelta(hours=1))
    with pytest.raises(CommitteeInputPolicyError, match="future-known"):
        validate(value, resolved(value, recorded_at=NOW + timedelta(seconds=1)))
    future = Input("snapshot:alpha", NOW + timedelta(seconds=1))
    with pytest.raises(CommitteeInputPolicyError, match="future-effective"):
        validate(future, resolved(future))


def test_canonical_current_correction_lineage_is_required() -> None:
    value = Input("snapshot:alpha", NOW - timedelta(hours=1))
    with pytest.raises(CommitteeInputPolicyError, match="superseded correction-lineage member"):
        validate(value, resolved(value, revision_id="revision:1"))
    with pytest.raises(CommitteeInputPolicyError, match="superseded committee input"):
        validate(value, resolved(value, superseded_at=NOW - timedelta(minutes=1)))
    with pytest.raises(CommitteeInputPolicyError, match="invalidated committee input"):
        validate(value, resolved(value, invalidated_at=NOW - timedelta(minutes=1)))


def test_family_freshness_policy_is_enforced() -> None:
    stale = Input("snapshot:alpha", NOW - timedelta(days=8))
    with pytest.raises(CommitteeInputPolicyError, match="stale committee input"):
        validate(stale, resolved(stale))
