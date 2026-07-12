from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition.collector import ProviderCollector
from hunter.acquisition.configuration import AcquisitionConfig
from hunter.acquisition.contracts import AcquisitionRepository, EvidenceCollector, EvidenceNormalizer, EvidenceProvider
from hunter.acquisition.models import (
    AcquisitionCheckpoint,
    AcquisitionRequest,
    AcquisitionRun,
    CacheEntry,
    RawEvidence,
)
from hunter.acquisition.normalizer import CanonicalEvidenceNormalizer
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.acquisition.validator import EvidenceAcquisitionValidator
from hunter.execution.identity import identity


class AcquisitionPipeline:
    def __init__(
        self,
        *,
        collector: EvidenceCollector | None = None,
        normalizer: EvidenceNormalizer | None = None,
        validator: EvidenceAcquisitionValidator | None = None,
        repository: AcquisitionRepository | None = None,
        config: AcquisitionConfig | None = None,
    ) -> None:
        self.collector = collector or ProviderCollector()
        self.normalizer = normalizer or CanonicalEvidenceNormalizer()
        self.config = config or AcquisitionConfig()
        self.validator = validator or EvidenceAcquisitionValidator(stale_after_seconds=self.config.stale_after_seconds)
        self.repository = repository or InMemoryAcquisitionRepository()
        self._cache: dict[str, CacheEntry] = {}

    def sync(self, provider: EvidenceProvider, request: AcquisitionRequest) -> AcquisitionRun:
        started_at = request.requested_at.astimezone(UTC)
        request = self._request_with_checkpoint(provider, request)
        raw = self._collect_with_cache(provider, request)
        normalized = self.normalizer.normalize(raw, request)
        validations = self.validator.validate(normalized, as_of=request.requested_at)
        self.repository.save_raw(raw)
        self.repository.save_normalized(normalized)
        self.repository.save_validations(validations)
        checkpoint = AcquisitionCheckpoint(
            provider=provider.metadata.name,
            domain=request.domain,
            target_id=request.target_id,
            cursor=_checkpoint_cursor(raw, request),
            updated_at=request.requested_at,
        )
        self.repository.save_checkpoint(checkpoint)
        run = AcquisitionRun(
            run_id=identity(
                "acquisition-run",
                {
                    "provider": provider.metadata.name,
                    "request": _request_payload(request),
                    "raw": tuple(item.raw_source_id for item in raw),
                },
            ),
            request=request,
            provider=provider.metadata.name,
            started_at=started_at,
            finished_at=request.requested_at,
            raw_count=len(raw),
            normalized_count=len(normalized),
            valid_count=sum(1 for item in validations if item.status == "valid"),
            duplicate_count=sum(1 for item in validations if item.status == "duplicate"),
            stale_count=sum(1 for item in validations if item.status == "stale"),
            invalid_count=sum(1 for item in validations if item.status == "invalid"),
            checkpoint=checkpoint,
        )
        self.repository.save_run(run)
        return run

    def _collect_with_cache(self, provider: EvidenceProvider, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        key = _cache_key(provider.metadata.name, request)
        if self.config.cache.enabled:
            cached = self._cache.get(key)
            if cached is not None and cached.fresh_at(request.requested_at):
                return cached.raw
        raw = self._retry_collect(provider, request)
        if self.config.cache.enabled:
            self._cache[key] = CacheEntry(
                key=key,
                raw=raw,
                created_at=request.requested_at,
                ttl_seconds=self.config.cache.ttl_seconds,
            )
        return raw

    def _retry_collect(self, provider: EvidenceProvider, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        errors: list[Exception] = []
        for _attempt in range(self.config.retry.max_attempts):
            try:
                return self.collector.collect(provider, request)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        raise errors[-1]

    def _request_with_checkpoint(
        self,
        provider: EvidenceProvider,
        request: AcquisitionRequest,
    ) -> AcquisitionRequest:
        if request.mode != "resume" or request.checkpoint:
            return request
        checkpoint = self.repository.latest_checkpoint(provider.metadata.name, request.domain, request.target_id)
        if checkpoint is None:
            return request
        return AcquisitionRequest(
            domain=request.domain,
            metric=request.metric,
            target_id=request.target_id,
            requested_at=request.requested_at,
            mode=request.mode,
            checkpoint=checkpoint.cursor,
            parameters=dict(request.parameters),
        )


def _cache_key(provider: str, request: AcquisitionRequest) -> str:
    return identity(
        "acquisition-cache",
        {
            "provider": provider,
            "domain": request.domain,
            "metric": request.metric,
            "target_id": request.target_id,
            "parameters": dict(request.parameters),
            "checkpoint": request.checkpoint,
            "mode": request.mode,
        },
    )


def _request_payload(request: AcquisitionRequest) -> dict[str, object]:
    return {
        "domain": request.domain,
        "metric": request.metric,
        "target_id": request.target_id,
        "requested_at": request.requested_at,
        "mode": request.mode,
        "checkpoint": request.checkpoint,
        "parameters": dict(request.parameters),
    }


def _checkpoint_cursor(raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> str:
    if not raw:
        return request.checkpoint or request.requested_at.isoformat()
    pages = [item.payload.get("page") for item in raw if isinstance(item.payload.get("page"), int)]
    if pages:
        return f"page:{max(pages)}"
    latest = max(item.retrieved_at for item in raw)
    return latest.isoformat()


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
