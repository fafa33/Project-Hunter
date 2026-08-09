from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hunter.evidence_intelligence.models import EvidenceSpan
from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidenceContextSelectionPolicy,
    EvidenceExtractionIntent,
    EvidencePreModelBuildResult,
    EvidencePromptSpecification,
    PreModelInvariantError,
    build_evidence_pre_model,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository


class CanonicalEvidenceSpanInventoryError(RuntimeError):
    """Raised when canonical repository-backed span inventory cannot be used safely."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CanonicalEvidenceSpanInventory:
    document_id: str
    spans: tuple[EvidenceSpan, ...]

    @property
    def span_ids(self) -> tuple[str, ...]:
        return tuple(span.span_id for span in self.spans)


def load_canonical_evidence_span_inventory(
    repository: EvidenceIntelligenceRepository,
    *,
    document_id: str,
) -> CanonicalEvidenceSpanInventory:
    """Read the exact current EvidenceSpan inventory from repository authority.

    This adapter is deliberately read-only and does not accept caller-prefiltered
    span tuples. The canonical `evidence_spans` table remains the source of truth.
    """

    with sqlite3.connect(repository.path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                span_id,
                document_id,
                source_evidence_id,
                normalized_content_hash,
                normalization_version,
                parser_id,
                rendition_id,
                offset_encoding,
                start_offset,
                end_offset,
                chunk_id,
                chunk_version,
                text_hash,
                excerpt,
                section_title,
                locator,
                span_status,
                created_at,
                validated_at
            FROM evidence_spans
            WHERE document_id = ?
            ORDER BY span_id
            """,
            (document_id,),
        ).fetchall()

    if not rows:
        raise CanonicalEvidenceSpanInventoryError("CANONICAL_EVIDENCE_SPAN_INVENTORY_EMPTY")

    spans = tuple(_evidence_span_from_row(row) for row in rows)
    return CanonicalEvidenceSpanInventory(document_id=document_id, spans=spans)


def build_evidence_pre_model_from_repository(
    *,
    repository: EvidenceIntelligenceRepository,
    document_id: str,
    execution_owner_id: str,
    intent: EvidenceExtractionIntent,
    policy: EvidenceContextSelectionPolicy,
    specification: EvidencePromptSpecification,
    capability: EvidenceCapabilityConstraint,
    retain_exact_prompt: bool = True,
) -> EvidencePreModelBuildResult:
    """Build from the repository-owned canonical EvidenceSpan inventory.

    Historical reconstruction is intentionally rejected here because the current
    `evidence_spans` table stores current span status rather than a versioned span
    lifecycle. Claiming a historical canonical inventory from that table would be
    dishonest. A future historical adapter must be backed by explicit source state.
    """

    if intent.historical_cutoff is not None:
        raise PreModelInvariantError("HISTORICAL_REPOSITORY_SPAN_INVENTORY_UNSUPPORTED")

    inventory = load_canonical_evidence_span_inventory(
        repository,
        document_id=document_id,
    )
    return build_evidence_pre_model(
        execution_owner_id=execution_owner_id,
        intent=intent,
        policy=policy,
        specification=specification,
        capability=capability,
        canonical_inventory=inventory.spans,
        candidate_span_ids=inventory.span_ids,
        retain_exact_prompt=retain_exact_prompt,
    )


def _evidence_span_from_row(row: sqlite3.Row) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=str(row["span_id"]),
        document_id=str(row["document_id"]),
        source_evidence_id=str(row["source_evidence_id"]),
        normalized_content_hash=str(row["normalized_content_hash"]),
        normalization_version=str(row["normalization_version"]),
        parser_id=str(row["parser_id"]),
        rendition_id=str(row["rendition_id"]),
        offset_encoding=str(row["offset_encoding"]),
        start_offset=int(row["start_offset"]),
        end_offset=int(row["end_offset"]),
        chunk_id=str(row["chunk_id"]),
        chunk_version=str(row["chunk_version"]),
        text_hash=str(row["text_hash"]),
        excerpt=str(row["excerpt"]),
        section_title=str(row["section_title"]),
        locator=str(row["locator"]),
        span_status=str(row["span_status"]),  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(str(row["created_at"])),
        validated_at=datetime.fromisoformat(str(row["validated_at"])),
    )
