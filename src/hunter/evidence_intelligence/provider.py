from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol

from hunter.evidence_intelligence.models import (
    AIProviderArtifact,
    AIProviderHealth,
    EvidenceSpan,
    ExtractionProposal,
    ExtractionSchema,
    SecurityAuditEvent,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.execution.identity import identity

PROVIDER_ARTIFACT_SCHEMA_VERSION = "ai-provider-artifact-v1"
PROVIDER_HEALTH_SCHEMA_VERSION = "ai-provider-health-v1"
EXTRACTION_PROPOSAL_SCHEMA_VERSION = "extraction-proposal-v1"
SECURITY_AUDIT_SCHEMA_VERSION = "security-audit-event-v1"
PROMPT_VERSION = "evidence-extraction-request-v1"

FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "tool_calls",
        "tools",
        "fetch",
        "fetch_requests",
        "http_requests",
        "repository_writes",
        "schema_changes",
        "configuration_changes",
        "config_changes",
    }
)

PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "system prompt",
    "developer message",
    "call this tool",
    "invoke tool",
    "use the browser",
    "fetch http",
    "write to the repository",
    "change configuration",
    "alter schema",
)


@dataclass(frozen=True)
class AIProviderMetadata:
    provider_name: str
    provider_version: str

    def __post_init__(self) -> None:
        _required("provider_name", self.provider_name)
        _required("provider_version", self.provider_version)


@dataclass(frozen=True)
class ExtractionRequest:
    document_id: str
    spans: tuple[EvidenceSpan, ...]
    schema: ExtractionSchema
    processing_run_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        _required("document_id", self.document_id)
        _required("processing_run_id", self.processing_run_id)
        if not self.spans:
            msg = "spans are required"
            raise ValueError(msg)
        if self.created_at.tzinfo is None:
            msg = "created_at must be timezone-aware"
            raise ValueError(msg)


@dataclass(frozen=True)
class ProviderExtractionResult:
    payload: Mapping[str, Any]
    status: str = "proposed"
    unavailable_reason: str = ""
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"proposed", "unavailable", "rejected"}:
            msg = "status must be proposed, unavailable, or rejected"
            raise ValueError(msg)
        if self.status == "proposed" and not self.payload:
            msg = "proposed provider result requires payload"
            raise ValueError(msg)


@dataclass(frozen=True)
class ProviderRunResult:
    artifact: AIProviderArtifact
    proposal: ExtractionProposal
    security_events: tuple[SecurityAuditEvent, ...] = field(default_factory=tuple)


class AIExtractionProvider(Protocol):
    @property
    def metadata(self) -> AIProviderMetadata:
        raise NotImplementedError

    def check_health(self, checked_at: datetime) -> AIProviderHealth:
        raise NotImplementedError

    def propose_extractions(self, request: ExtractionRequest) -> ProviderExtractionResult:
        raise NotImplementedError


class PromptInjectionDetector:
    def detect(self, spans: Sequence[EvidenceSpan]) -> tuple[str, ...]:
        findings: list[str] = []
        for span in spans:
            lowered = span.excerpt.lower()
            for pattern in PROMPT_INJECTION_PATTERNS:
                if pattern in lowered:
                    findings.append(f"{span.span_id}:{pattern}")
        return tuple(findings)


class SecureAIProviderRunner:
    def __init__(
        self,
        repository: EvidenceIntelligenceRepository,
        *,
        detector: PromptInjectionDetector | None = None,
    ) -> None:
        self.repository = repository
        self.detector = detector or PromptInjectionDetector()

    def run(
        self,
        provider: AIExtractionProvider,
        request: ExtractionRequest,
    ) -> ProviderRunResult:
        self.repository.save_extraction_schema(request.schema)
        findings = self.detector.detect(request.spans)
        if findings:
            return self._reject_for_security(provider.metadata, request, findings)

        health = provider.check_health(request.created_at)
        self.repository.save_provider_health(health)
        if health.status == "unavailable":
            return self._unavailable(provider.metadata, request, health.unavailable_reason or health.failure_type)

        try:
            result = provider.propose_extractions(request)
        except Exception as exc:  # pragma: no cover - exception type is provider-specific.
            reason = f"provider_exception:{exc.__class__.__name__}"
            health = AIProviderHealth(
                health_id=identity(
                    "evidence-provider-health",
                    {
                        "provider_name": provider.metadata.provider_name,
                        "provider_version": provider.metadata.provider_version,
                        "checked_at": request.created_at,
                        "failure_type": reason,
                    },
                ),
                provider_name=provider.metadata.provider_name,
                provider_version=provider.metadata.provider_version,
                status="unavailable",
                checked_at=request.created_at,
                latency_ms=None,
                failure_type=reason,
                unavailable_reason=reason,
                schema_version=PROVIDER_HEALTH_SCHEMA_VERSION,
            )
            self.repository.save_provider_health(health)
            return self._unavailable(provider.metadata, request, reason)

        if _contains_forbidden_response_capability(result.payload):
            return self._reject_for_security(
                provider.metadata,
                request,
                ("provider_output_requested_forbidden_capability",),
            )
        if result.status == "unavailable":
            return self._unavailable(provider.metadata, request, result.unavailable_reason or "provider_unavailable")
        if result.status == "rejected":
            return self._rejected(provider.metadata, request, result.rejection_reason or "provider_rejected")
        return self._proposed(provider.metadata, request, result.payload)

    def _proposed(
        self,
        metadata: AIProviderMetadata,
        request: ExtractionRequest,
        payload: Mapping[str, Any],
    ) -> ProviderRunResult:
        artifact = _artifact(metadata, request, "proposed", payload)
        proposal = _proposal(metadata, request, artifact, "proposed", payload)
        self.repository.save_provider_artifact(artifact)
        self.repository.save_extraction_proposal(proposal)
        return ProviderRunResult(artifact=artifact, proposal=proposal)

    def _unavailable(
        self,
        metadata: AIProviderMetadata,
        request: ExtractionRequest,
        reason: str,
    ) -> ProviderRunResult:
        payload = {"unavailable_reason": reason}
        artifact = _artifact(metadata, request, "unavailable", payload)
        proposal = _proposal(metadata, request, artifact, "unavailable", payload, unavailable_reason=reason)
        self.repository.save_provider_artifact(artifact)
        self.repository.save_extraction_proposal(proposal)
        return ProviderRunResult(artifact=artifact, proposal=proposal)

    def _rejected(
        self,
        metadata: AIProviderMetadata,
        request: ExtractionRequest,
        reason: str,
    ) -> ProviderRunResult:
        payload = {"rejection_reason": reason}
        artifact = _artifact(metadata, request, "rejected", payload)
        proposal = _proposal(metadata, request, artifact, "rejected", payload, rejection_reason=reason)
        self.repository.save_provider_artifact(artifact)
        self.repository.save_extraction_proposal(proposal)
        return ProviderRunResult(artifact=artifact, proposal=proposal)

    def _reject_for_security(
        self,
        metadata: AIProviderMetadata,
        request: ExtractionRequest,
        findings: tuple[str, ...],
    ) -> ProviderRunResult:
        reason = ";".join(findings)
        event = SecurityAuditEvent(
            event_id=identity(
                "evidence-security-audit-event",
                {
                    "document_id": request.document_id,
                    "event_type": "prompt_injection_detected",
                    "reason": reason,
                    "created_at": request.created_at,
                },
            ),
            document_id=request.document_id,
            event_type="prompt_injection_detected",
            detected_at=request.created_at,
            severity="high",
            reason=reason,
            schema_version=SECURITY_AUDIT_SCHEMA_VERSION,
        )
        self.repository.save_security_event(event)
        result = self._rejected(metadata, request, "security_boundary_rejected_untrusted_content")
        return ProviderRunResult(artifact=result.artifact, proposal=result.proposal, security_events=(event,))


def extraction_schema(
    *,
    name: str,
    purpose: str,
    schema_version: str,
    output_contract: Mapping[str, Any],
    created_at: datetime,
) -> ExtractionSchema:
    contract = _canonical_json(output_contract)
    return ExtractionSchema(
        schema_id=identity(
            "evidence-extraction-schema",
            {"name": name, "schema_version": schema_version, "output_contract": contract},
        ),
        name=name,
        purpose=purpose,
        schema_version=schema_version,
        output_contract=contract,
        content_hash=_digest(contract),
        created_at=created_at,
    )


def _artifact(
    metadata: AIProviderMetadata,
    request: ExtractionRequest,
    status: str,
    payload: Mapping[str, Any],
) -> AIProviderArtifact:
    payload_hash = _digest(_canonical_json(payload))
    artifact_id = identity(
        "evidence-ai-provider-artifact",
        {
            "document_id": request.document_id,
            "provider_name": metadata.provider_name,
            "provider_version": metadata.provider_version,
            "schema_id": request.schema.schema_id,
            "payload_hash": payload_hash,
            "status": status,
        },
    )
    return AIProviderArtifact(
        artifact_id=artifact_id,
        processing_run_id=request.processing_run_id,
        provider_name=metadata.provider_name,
        provider_version=metadata.provider_version,
        schema_version=PROVIDER_ARTIFACT_SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        content_hash=payload_hash,
        status=status,
        created_at=request.created_at,
    )


def _proposal(
    metadata: AIProviderMetadata,
    request: ExtractionRequest,
    artifact: AIProviderArtifact,
    status: str,
    payload: Mapping[str, Any],
    *,
    unavailable_reason: str = "",
    rejection_reason: str = "",
) -> ExtractionProposal:
    payload_hash = _digest(_canonical_json(payload))
    return ExtractionProposal(
        proposal_id=identity(
            "evidence-extraction-proposal",
            {
                "artifact_id": artifact.artifact_id,
                "document_id": request.document_id,
                "schema_id": request.schema.schema_id,
                "schema_version": request.schema.schema_version,
            },
        ),
        artifact_id=artifact.artifact_id,
        document_id=request.document_id,
        schema_id=request.schema.schema_id,
        schema_version=request.schema.schema_version,
        provider_name=metadata.provider_name,
        provider_version=metadata.provider_version,
        status=status,
        proposed_payload_hash=payload_hash,
        created_at=request.created_at,
        unavailable_reason=unavailable_reason,
        rejection_reason=rejection_reason,
    )


def _contains_forbidden_response_capability(payload: Mapping[str, Any]) -> bool:
    for key, value in payload.items():
        if str(key) in FORBIDDEN_RESPONSE_KEYS:
            return True
        if isinstance(value, Mapping) and _contains_forbidden_response_capability(value):
            return True
        if isinstance(value, list) and any(
            isinstance(item, Mapping) and _contains_forbidden_response_capability(item) for item in value
        ):
            return True
    return False


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _required(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)
