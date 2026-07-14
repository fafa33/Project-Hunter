from __future__ import annotations

from typing import Any

from hunter.execution.identity import identity


def competitive_id(kind: str, payload: Any) -> str:
    return identity(f"competitive-{kind}", payload)


def competitive_relationship_id(
    *,
    subject_candidate_id: str,
    peer_candidate_id: str,
    relationship_type: str,
    claim_id: str,
    scope: str,
    schema_version: str,
) -> str:
    return competitive_id(
        "relationship",
        {
            "subject_candidate_id": subject_candidate_id,
            "peer_candidate_id": peer_candidate_id,
            "relationship_type": relationship_type,
            "claim_id": claim_id,
            "scope": scope,
            "schema_version": schema_version,
        },
    )


def algorithmic_peer_relationship_id(
    *,
    subject_candidate_id: str,
    peer_candidate_id: str,
    relationship_type: str,
    policy_id: str,
    policy_version: str,
    scope: str,
) -> str:
    return competitive_id(
        "algorithmic-peer-relationship",
        {
            "subject_candidate_id": subject_candidate_id,
            "peer_candidate_id": peer_candidate_id,
            "relationship_type": relationship_type,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "scope": scope,
        },
    )


def peer_set_id(
    *,
    subject_candidate_id: str,
    scope: str,
    peer_set_version: str,
    policy_id: str,
    policy_version: str,
) -> str:
    return competitive_id(
        "peer-set",
        {
            "subject_candidate_id": subject_candidate_id,
            "scope": scope,
            "peer_set_version": peer_set_version,
            "policy_id": policy_id,
            "policy_version": policy_version,
        },
    )
