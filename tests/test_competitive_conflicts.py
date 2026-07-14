from __future__ import annotations

from datetime import UTC, datetime

from hunter.competitive import (
    CompetitiveConflictDetector,
    CompetitiveRelationship,
    CompetitiveReplayContext,
    CompetitiveReplayQuery,
    CompetitiveRepository,
)

JAN_1 = datetime(2026, 1, 1, tzinfo=UTC)
JAN_15 = datetime(2026, 1, 15, tzinfo=UTC)
FEB_1 = datetime(2026, 2, 1, tzinfo=UTC)


def test_phase_seven_conflict_detection_is_scope_peer_modality_and_time_aware(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    detector = CompetitiveConflictDetector(repository)

    no_conflicts = detector.detect(
        (
            relationship("claim-1", polarity="positive", scope="ethereum", valid_to=JAN_15),
            relationship("claim-2", polarity="negative", scope="polygon"),
            relationship("claim-3", polarity="negative", peer_candidate_id="candidate-c", scope="ethereum"),
            relationship("claim-4", polarity="negative", scope="ethereum", valid_from=FEB_1),
        )
    )
    conflict = detector.detect(
        (
            relationship("claim-5", polarity="positive", scope="ethereum"),
            relationship("claim-6", polarity="negative", scope="ethereum"),
        )
    )

    assert no_conflicts == ()
    assert len(conflict) == 1
    assert conflict[0].status == "detected"
    assert repository.count("competitive_conflict_links") == 2


def test_phase_seven_replay_query_labels_strict_and_reconstructed_modes(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    repository.save_competitive_relationship(relationship("claim-1", recorded_at=FEB_1))
    query = CompetitiveReplayQuery(repository)

    strict = query.view_for_subject(
        "candidate-a",
        CompetitiveReplayContext(cutoff=JAN_15, strict_known_by_hunter=True),
    )
    reconstructed = query.view_for_subject(
        "candidate-a",
        CompetitiveReplayContext(cutoff=JAN_15, strict_known_by_hunter=False),
    )

    assert strict.mode == "historical_strict_known_by_hunter"
    assert strict.known_at_cutoff is True
    assert strict.relationships == ()
    assert reconstructed.mode == "reconstructed_after_cutoff"
    assert reconstructed.known_at_cutoff is False
    assert len(reconstructed.relationships) == 1


def test_phase_seven_resolved_or_superseded_conflicts_preserve_prior_links(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    detector = CompetitiveConflictDetector(repository)
    first = detector.detect(
        (
            relationship("claim-1", polarity="positive"),
            relationship("claim-2", polarity="negative"),
        ),
        detected_at=JAN_1,
    )
    second = detector.detect(
        (
            relationship("claim-1", polarity="positive"),
            relationship("claim-3", polarity="negative", status="superseded"),
        ),
        detected_at=FEB_1,
    )

    assert first[0].status == "detected"
    assert second[0].status == "resolved"
    assert first[0].conflict_id != second[0].conflict_id
    assert repository.count("competitive_conflict_links") == 4
    first_links = repository.conflict_links_for_relationship(first[0].left_relationship_id, cutoff=JAN_15)
    assert first_links[0]["conflict_id"] == first[0].conflict_id


def relationship(
    claim_id: str,
    *,
    polarity: str = "positive",
    scope: str = "global",
    peer_candidate_id: str = "candidate-b",
    status: str = "active",
    valid_from: datetime | None = JAN_1,
    valid_to: datetime | None = None,
    recorded_at: datetime = JAN_1,
) -> CompetitiveRelationship:
    return CompetitiveRelationship(
        relationship_id=f"relationship-{claim_id}",
        subject_candidate_id="candidate-a",
        peer_candidate_id=peer_candidate_id,
        relationship_type="direct_competitor",
        status=status,  # type: ignore[arg-type]
        predicate_id="competes_with",
        predicate_schema_version="competitive-predicate-v1",
        claim_id=claim_id,
        subject_entity_id="entity-a",
        peer_entity_id=f"entity-{peer_candidate_id}",
        scope=scope,
        modality="asserted",
        polarity=polarity,  # type: ignore[arg-type]
        confidence=0.8,
        freshness=0.9,
        effective_at=valid_from or JAN_1,
        recorded_at=recorded_at,
        schema_version="competitive-relationship-v1",
        valid_from=valid_from,
        valid_to=valid_to,
    )
