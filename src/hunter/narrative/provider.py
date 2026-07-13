from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from hunter.acquisition.models import (
    AcquisitionRequest,
    EvidenceValidation,
    NormalizedEvidence,
    ProviderHealth,
    ProviderMetadata,
    RateLimit,
    RawEvidence,
    ValidationIssue,
)
from hunter.execution.identity import identity
from hunter.narrative.configuration import SUPPORTED_NARRATIVE_PROVIDERS, NarrativeSourceConfig

NARRATIVE_METRIC = "narrative_item"
NARRATIVE_ENGINES: tuple[str, ...] = (
    "news",
    "social",
    "narrative",
    "future_demand",
    "macro_intelligence",
    "opportunity_timing",
    "pattern_matching",
    "capital_rotation",
)

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "artificial intelligence", "agent"),
    "depin": ("depin", "physical infrastructure", "compute", "wireless"),
    "defi": ("defi", "liquidity", "lending", "dex", "yield"),
    "restaking": ("restaking", "eigenlayer", "avs"),
    "layer_2": ("layer 2", "l2", "rollup", "sequencer"),
    "data_availability": ("data availability", "da layer", "celestia"),
    "roadmap": ("roadmap", "milestone", "mainnet", "testnet", "upgrade"),
}

EVIDENCE_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "protocol_release": ("release", "version", "upgrade", "mainnet", "testnet"),
    "governance": ("governance", "proposal", "vote", "dao"),
    "partnership": ("partnership", "partner", "collaboration"),
    "integration": ("integration", "integrates", "integrated", "support"),
    "exchange_listing": ("listing", "listed", "exchange"),
    "security_event": ("security", "audit", "vulnerability", "incident"),
    "exploit": ("exploit", "hack", "attack"),
    "ecosystem_expansion": ("ecosystem", "launch", "expansion", "grant"),
    "developer_activity": ("commit", "pull request", "merge", "tag", "release"),
    "roadmap_milestone": ("roadmap", "milestone", "phase"),
}

CHAIN_KEYWORDS: tuple[str, ...] = (
    "ethereum",
    "bitcoin",
    "solana",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
    "cosmos",
    "polkadot",
)

TECHNOLOGY_KEYWORDS: tuple[str, ...] = (
    "zk",
    "zero knowledge",
    "rollup",
    "bridge",
    "oracle",
    "data availability",
    "restaking",
    "staking",
    "smart contract",
)

VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"\$[A-Z][A-Z0-9]{1,12}\b")


@dataclass(frozen=True)
class NarrativeProviderConfig:
    sources: tuple[NarrativeSourceConfig, ...] = ()
    request_timeout_seconds: int = 30


class NarrativeProvider:
    def __init__(self, config: NarrativeProviderConfig | None = None) -> None:
        self.config = config or NarrativeProviderConfig()
        self._last_sync: datetime | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="narrative",
            capabilities=("narrative", "news", "social", "macro", "pattern"),
            supported_metrics=(NARRATIVE_METRIC,),
            rate_limits=(RateLimit(requests=1, window_seconds=1),),
            last_sync=self._last_sync,
            availability="available",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name="narrative",
            availability="available",
            checked_at=datetime.now(tz=UTC),
            last_sync=self._last_sync,
            message=f"sources={len(self.config.sources)}",
        )

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        rows: list[RawEvidence] = []
        page = _checkpoint_page(request.checkpoint)
        for index, source in enumerate((item for item in self.config.sources if item.enabled), start=1):
            if index < page:
                continue
            if source.provider not in SUPPORTED_NARRATIVE_PROVIDERS:
                rows.append(_invalid_provider_row(source, request, index))
                continue
            try:
                items = _parse_feed(_read_source(source, self.config.request_timeout_seconds))
            except (OSError, ET.ParseError, UnicodeDecodeError, ValueError) as exc:
                rows.append(_source_error_row(source, request, index, exc))
                continue
            for item_index, item in enumerate(items):
                payload = _payload(source, item, page=index, item_index=item_index)
                rows.append(
                    RawEvidence(
                        provider="narrative",
                        collector=f"narrative-{source.provider}-collector",
                        raw_source_id=str(payload["duplicate_hash"]),
                        domain="narrative",
                        metric=NARRATIVE_METRIC,
                        target_id=source.project_id,
                        retrieved_at=request.requested_at,
                        payload=payload,
                        source_url=str(payload["url"]),
                        repository_id=f"narrative:{source.project_id}:{source.provider}:{payload['content_hash']}",
                    )
                )
        self._last_sync = request.requested_at
        return tuple(rows)


class NarrativeEvidenceNormalizer:
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        evidence = []
        for item in raw:
            text = f"{item.payload.get('title', '')} {item.payload.get('description', '')}"
            extracted = _extract(text, str(item.payload.get("project", "")))
            confidence = _completeness(item.payload)
            evidence_id = identity(
                "narrative-evidence",
                {
                    "project": item.target_id,
                    "duplicate_hash": item.payload.get("duplicate_hash"),
                    "collector_metadata": item.payload.get("collector_metadata"),
                    "timestamp": item.payload.get("timestamp"),
                },
            )
            evidence.append(
                NormalizedEvidence(
                    evidence_id=evidence_id,
                    repository_id=item.repository_id,
                    provider=item.provider,
                    collector=item.collector,
                    raw_source_id=item.raw_source_id,
                    domain=item.domain,
                    metric=item.metric,
                    target_id=item.target_id,
                    value=str(item.payload.get("url", item.raw_source_id)),
                    raw_metrics={**dict(item.payload), **extracted},
                    normalized_metrics={
                        "schema_completeness": confidence,
                        "topic_count": _scale(len(extracted["topics"]), 10),
                        "entity_count": _scale(len(extracted["entities"]), 10),
                        "evidence_category_count": _scale(len(extracted["evidence_categories"]), 10),
                        "roadmap_reference": 1.0 if extracted["roadmap_references"] else 0.0,
                        "version_reference": 1.0 if extracted["version_references"] else 0.0,
                        "technology_reference": 1.0 if extracted["technology_references"] else 0.0,
                    },
                    source_url=str(item.payload.get("url", "")),
                    retrieved_at=item.retrieved_at,
                    normalized_at=request.requested_at,
                    confidence=confidence,
                    freshness=_freshness(str(item.payload.get("timestamp", "")), request.requested_at),
                    raw_evidence_id=item.raw_source_id,
                )
            )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))


class NarrativeEvidenceValidator:
    def __init__(self, *, expired_after_days: int = 365) -> None:
        self.expired_after_days = expired_after_days

    def validate(
        self,
        evidence: tuple[NormalizedEvidence, ...],
        *,
        as_of: object,
    ) -> tuple[EvidenceValidation, ...]:
        if not isinstance(as_of, datetime):
            msg = "as_of must be a datetime"
            raise ValueError(msg)
        seen: set[str] = set()
        rows = []
        for item in evidence:
            issues: list[ValidationIssue] = []
            duplicate_hash = str(item.raw_metrics.get("duplicate_hash", ""))
            if duplicate_hash in seen:
                issues.append(ValidationIssue("duplicate", "duplicate_hash", "duplicate narrative item"))
            seen.add(duplicate_hash)
            _validate_required(item, issues)
            _validate_url(str(item.raw_metrics.get("url", "")), issues)
            _validate_timestamp(str(item.raw_metrics.get("timestamp", "")), as_of, self.expired_after_days, issues)
            if str(item.raw_metrics.get("provider", "")) not in SUPPORTED_NARRATIVE_PROVIDERS:
                issues.append(ValidationIssue("provider", "provider", "unknown narrative provider"))
            if not _valid_language(str(item.raw_metrics.get("language", ""))):
                issues.append(ValidationIssue("encoding", "language", "invalid language encoding"))
            status = "valid"
            if any(issue.code == "duplicate" for issue in issues):
                status = "duplicate"
            elif any(issue.code == "expired" for issue in issues):
                status = "stale"
            elif issues:
                status = "invalid"
            rows.append(
                EvidenceValidation(
                    evidence_id=item.evidence_id,
                    status=status,  # type: ignore[arg-type]
                    validated_at=as_of,
                    confidence=0.0 if status == "invalid" else item.confidence,
                    freshness=0.0 if status == "invalid" else item.freshness,
                    issues=tuple(issues),
                )
            )
        return tuple(sorted(rows, key=lambda item: item.evidence_id))


def _read_source(source: NarrativeSourceConfig, timeout: int) -> str:
    parsed = urllib.parse.urlparse(source.url)
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path)).read_text(encoding="utf-8")
    path = Path(source.url)
    if parsed.scheme == "" and path.exists():
        return path.read_text(encoding="utf-8")
    request = urllib.request.Request(source.url, headers={"User-Agent": "Project-Hunter/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _parse_feed(text: str) -> tuple[dict[str, str], ...]:
    root = ET.fromstring(text)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return tuple(_parse_item(item) for item in items)


def _parse_item(item: ET.Element) -> dict[str, str]:
    title = _text(item, "title")
    description = _text(item, "description") or _text(item, "summary") or _text(item, "content")
    url = _text(item, "link")
    if not url:
        link = item.find("{http://www.w3.org/2005/Atom}link")
        url = str(link.attrib.get("href", "")) if link is not None else ""
    timestamp = _text(item, "pubDate") or _text(item, "published") or _text(item, "updated")
    author = _text(item, "author")
    return {
        "title": title,
        "description": description,
        "url": url,
        "timestamp": _timestamp(timestamp),
        "author": author,
    }


def _text(item: ET.Element, tag: str) -> str:
    found = item.find(tag)
    if found is None:
        found = item.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    return (found.text or "").strip() if found is not None else ""


def _timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return value


def _payload(source: NarrativeSourceConfig, item: dict[str, str], *, page: int, item_index: int) -> dict[str, Any]:
    title = item["title"]
    description = item["description"]
    url = item["url"]
    timestamp = item["timestamp"]
    content_hash = _hash(f"{title}\n{description}\n{timestamp}")
    duplicate_hash = _hash(url or f"{title}\n{timestamp}")
    return {
        "title": title,
        "description": description,
        "url": url,
        "timestamp": timestamp,
        "project": source.project_id,
        "source": source.source,
        "provider": source.provider,
        "language": source.language,
        "author": item.get("author") or source.author,
        "tags": source.tags,
        "categories": source.categories,
        "content_hash": content_hash,
        "duplicate_hash": duplicate_hash,
        "collector_metadata": {"source_id": source.source_id, "page": page, "item_index": item_index},
        "page": page,
    }


def _invalid_provider_row(source: NarrativeSourceConfig, request: AcquisitionRequest, page: int) -> RawEvidence:
    payload = {
        "title": source.source_id,
        "description": "",
        "url": source.url,
        "timestamp": request.requested_at.isoformat(),
        "project": source.project_id,
        "source": source.source,
        "provider": source.provider,
        "language": source.language,
        "author": source.author,
        "tags": source.tags,
        "categories": source.categories,
        "content_hash": _hash(source.source_id),
        "duplicate_hash": _hash(source.source_id),
        "collector_metadata": {"source_id": source.source_id, "page": page},
        "page": page,
    }
    return RawEvidence(
        provider="narrative",
        collector="narrative-unknown-collector",
        raw_source_id=str(payload["duplicate_hash"]),
        domain="narrative",
        metric=NARRATIVE_METRIC,
        target_id=source.project_id or "unknown",
        retrieved_at=request.requested_at,
        payload=payload,
        source_url=source.url,
        repository_id=f"narrative:{source.project_id or 'unknown'}:{source.provider}:{payload['content_hash']}",
    )


def _source_error_row(
    source: NarrativeSourceConfig, request: AcquisitionRequest, page: int, error: BaseException
) -> RawEvidence:
    payload = {
        "title": source.source_id,
        "description": "",
        "url": source.url,
        "timestamp": request.requested_at.isoformat(),
        "project": source.project_id,
        "source": source.source,
        "provider": source.provider,
        "language": source.language,
        "author": source.author,
        "tags": source.tags,
        "categories": source.categories,
        "content_hash": _hash(f"{source.source_id}:{type(error).__name__}"),
        "duplicate_hash": _hash(source.source_id),
        "collector_metadata": {"source_id": source.source_id, "page": page},
        "page": page,
        "fetch_error": type(error).__name__,
        "fetch_error_message": str(error),
    }
    return RawEvidence(
        provider="narrative",
        collector=f"narrative-{source.provider}-collector",
        raw_source_id=str(payload["duplicate_hash"]),
        domain="narrative",
        metric=NARRATIVE_METRIC,
        target_id=source.project_id or "unknown",
        retrieved_at=request.requested_at,
        payload=payload,
        source_url=source.url,
        repository_id=f"narrative:{source.project_id or 'unknown'}:{source.provider}:{payload['content_hash']}",
    )


def _extract(text: str, project: str) -> dict[str, tuple[str, ...]]:
    lowered = text.lower()
    topics = tuple(sorted(topic for topic, words in TOPIC_KEYWORDS.items() if any(word in lowered for word in words)))
    evidence_categories = tuple(
        sorted(
            category for category, words in EVIDENCE_CATEGORY_KEYWORDS.items() if any(word in lowered for word in words)
        )
    )
    chains = tuple(sorted(chain for chain in CHAIN_KEYWORDS if chain in lowered))
    technologies = tuple(sorted(word for word in TECHNOLOGY_KEYWORDS if word in lowered))
    versions = tuple(sorted(set(VERSION_RE.findall(text))))
    tokens = tuple(sorted(set(TOKEN_RE.findall(text))))
    words = tuple(sorted(set(re.findall(r"\b[a-z][a-z0-9-]{3,}\b", lowered))))[:20]
    project_refs = (project,) if project and project.lower() in lowered else ()
    roadmap_refs = tuple(
        topic for topic in ("roadmap", "mainnet", "testnet", "upgrade", "milestone") if topic in lowered
    )
    entities = tuple(sorted(set((*chains, *technologies, *tokens, *project_refs))))
    return {
        "topics": topics,
        "entities": entities,
        "protocols": project_refs,
        "chains": chains,
        "narratives": topics,
        "categories": topics,
        "evidence_categories": evidence_categories,
        "keywords": words,
        "project_references": project_refs,
        "technology_references": technologies,
        "token_references": tokens,
        "version_references": versions,
        "roadmap_references": roadmap_refs,
    }


def _validate_required(item: NormalizedEvidence, issues: list[ValidationIssue]) -> None:
    if item.raw_metrics.get("fetch_error"):
        issues.append(ValidationIssue("source", "url", "source could not be fetched or parsed"))
    for field in ("title", "url", "timestamp", "project", "source", "provider", "content_hash", "duplicate_hash"):
        value = item.raw_metrics.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(ValidationIssue("missing", field, f"missing {field}"))


def _validate_url(value: str, issues: list[ValidationIssue]) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https", "file"}:
        issues.append(ValidationIssue("url", "url", "broken or unsupported URL"))
    elif parsed.scheme in {"http", "https"} and not parsed.netloc:
        issues.append(ValidationIssue("url", "url", "broken URL"))
    elif parsed.scheme == "file" and not parsed.path:
        issues.append(ValidationIssue("url", "url", "broken file URL"))


def _validate_timestamp(value: str, as_of: datetime, expired_after_days: int, issues: list[ValidationIssue]) -> None:
    if not value:
        issues.append(ValidationIssue("timestamp", "timestamp", "missing timestamp"))
        return
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        issues.append(ValidationIssue("timestamp", "timestamp", "malformed timestamp"))
        return
    if timestamp > as_of:
        issues.append(ValidationIssue("timestamp", "timestamp", "timestamp is in the future"))
    if (as_of - timestamp).days > expired_after_days:
        issues.append(ValidationIssue("expired", "timestamp", "expired content"))


def _valid_language(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return bool(re.fullmatch(r"[a-z]{2}(?:-[A-Za-z]{2})?", value.strip()))


def _completeness(payload: dict[str, Any]) -> float:
    fields = ("title", "url", "timestamp", "project", "source", "provider", "content_hash", "duplicate_hash")
    present = sum(1 for field in fields if payload.get(field))
    return round(present / len(fields), 4)


def _freshness(timestamp: str, as_of: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return 0.0
    age_days = max((as_of - parsed).total_seconds() / 86_400, 0.0)
    return round(max(0.0, min(1.0, 1.0 - (age_days / 365.0))), 4)


def _checkpoint_page(checkpoint: str | None) -> int:
    if not checkpoint:
        return 1
    try:
        return max(1, int(checkpoint.split(":", 1)[-1]))
    except ValueError:
        return 1


def _scale(value: int, maximum: int) -> float:
    return round(min(max(value, 0), maximum) / maximum, 4)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
