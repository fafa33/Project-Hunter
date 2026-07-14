from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository


@dataclass(frozen=True)
class ReportContext:
    cutoff: datetime | None = None
    strict_known_by_hunter: bool = False

    @property
    def mode(self) -> str:
        if self.cutoff is None:
            return "current"
        if self.strict_known_by_hunter:
            return "historical_strict_known_by_hunter"
        return "reconstructed_after_cutoff"


class EvidenceIntelligenceReporter:
    def __init__(self, repository: EvidenceIntelligenceRepository) -> None:
        self.repository = repository

    def coverage(self) -> dict[str, int | str]:
        documents = self.repository.count("evidence_documents")
        spans = self.repository.count("evidence_spans")
        claims = self.repository.count("knowledge_claims")
        conflicts = self.repository.count("knowledge_conflicts")
        relationships = self.repository.count("knowledge_relationship_projections")
        authority_events = self.repository.count("source_authority_verification_events")
        return {
            "mode": "current",
            "documents": documents,
            "spans": spans,
            "claims": claims,
            "conflicts": conflicts,
            "relationships": relationships,
            "authority_events": authority_events,
        }

    def source_authority(self, context: ReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for document in self.repository.documents():
            status = str(document["authority_status"])
            if context.cutoff is not None:
                status = (
                    self.repository.authority_status_at(
                        str(document["document_id"]),
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                    or "unavailable"
                )
            rows.append(
                {
                    "mode": context.mode,
                    "document_id": str(document["document_id"]),
                    "candidate_id": str(document["candidate_id"]),
                    "authority_status": status,
                    "known_at_cutoff": str(context.cutoff is None or context.strict_known_by_hunter).lower(),
                }
            )
        return tuple(rows)

    def document_lifecycle(self, context: ReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for document in self.repository.documents():
            status = str(document["document_status"])
            if context.cutoff is not None:
                status = (
                    self.repository.document_status_at(
                        str(document["document_id"]),
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                    or "unavailable"
                )
            rows.append(
                {
                    "mode": context.mode,
                    "document_id": str(document["document_id"]),
                    "status": status,
                    "known_at_cutoff": str(context.cutoff is None or context.strict_known_by_hunter).lower(),
                }
            )
        return tuple(rows)

    def claim_lifecycle(self, context: ReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for claim in self.repository.claims():
            status = str(claim["status"])
            if context.cutoff is not None:
                status = (
                    self.repository.claim_status_at(
                        str(claim["claim_id"]),
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                    or "unavailable"
                )
            rows.append(
                {
                    "mode": context.mode,
                    "claim_id": str(claim["claim_id"]),
                    "predicate_id": str(claim["predicate_id"]),
                    "status": status,
                    "confidence": str(claim["confidence"]),
                    "known_at_cutoff": str(context.cutoff is None or context.strict_known_by_hunter).lower(),
                }
            )
        return tuple(rows)

    def conflict_lifecycle(self, context: ReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for conflict in self.repository.conflicts():
            status = str(conflict["status"])
            if context.cutoff is not None:
                status = (
                    self.repository.conflict_status_at(
                        str(conflict["conflict_id"]),
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                    or "unavailable"
                )
            rows.append(
                {
                    "mode": context.mode,
                    "conflict_id": str(conflict["conflict_id"]),
                    "predicate_id": str(conflict["predicate_id"]),
                    "status": status,
                    "known_at_cutoff": str(context.cutoff is None or context.strict_known_by_hunter).lower(),
                }
            )
        return tuple(rows)

    def candidate_explain(self, candidate_id: str, context: ReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for claim in self.repository.claims():
            if str(claim.get("subject_candidate_id")) != candidate_id:
                continue
            status = str(claim["status"])
            if context.cutoff is not None:
                status = (
                    self.repository.claim_status_at(
                        str(claim["claim_id"]),
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                    or "unavailable"
                )
            lineage = self.repository.claim_lineage(str(claim["claim_id"]))
            rows.append(
                {
                    "mode": context.mode,
                    "candidate_id": candidate_id,
                    "claim_id": str(claim["claim_id"]),
                    "predicate_id": str(claim["predicate_id"]),
                    "status": status,
                    "evidence_count": str(len(lineage["source_evidence"])),
                    "span_count": str(len(lineage["spans"])),
                    "conflict_count": str(len(lineage["conflicts"])),
                    "known_at_cutoff": str(context.cutoff is None or context.strict_known_by_hunter).lower(),
                }
            )
        return tuple(rows)

    def security_audit(self) -> tuple[dict[str, str], ...]:
        rows = []
        for document in self.repository.documents():
            for event in self.repository.security_events(str(document["document_id"])):
                rows.append(
                    {
                        "document_id": str(event["document_id"]),
                        "event_type": str(event["event_type"]),
                        "severity": str(event["severity"]),
                        "detected_at": str(event["detected_at"]),
                        "reason": str(event["reason"]),
                    }
                )
        return tuple(rows)

    def provider_health(self) -> tuple[dict[str, str], ...]:
        rows = []
        for event in self.repository.provider_health():
            rows.append(
                {
                    "provider": str(event["provider_name"]),
                    "version": str(event["provider_version"]),
                    "status": str(event["status"]),
                    "checked_at": str(event["checked_at"]),
                    "latency_ms": str(event["latency_ms"] if event["latency_ms"] is not None else "unavailable"),
                    "failure_type": str(event["failure_type"] or "none"),
                    "unavailable_reason": str(event["unavailable_reason"] or "none"),
                }
            )
        return tuple(rows)
