from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_NARRATIVE_PROVIDERS: tuple[str, ...] = (
    "rss",
    "official_blog",
    "official_announcement",
    "github_releases",
    "github_discussions",
    "github_tags",
    "official_docs",
)

FUTURE_NARRATIVE_PROVIDERS: tuple[str, ...] = (
    "x",
    "reddit",
    "telegram",
    "discord",
    "youtube",
    "podcasts",
    "governance_forums",
)


@dataclass(frozen=True)
class NarrativeSourceConfig:
    source_id: str
    provider: str
    project_id: str
    url: str
    source: str
    language: str = "en"
    author: str = ""
    tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class NarrativeAcquisitionConfig:
    enabled: bool = True
    expired_after_days: int = 365
    sources: tuple[NarrativeSourceConfig, ...] = ()


def load_narrative_config(path: str | Path = "configs/narrative_sources.yaml") -> NarrativeAcquisitionConfig:
    config_path = Path(path)
    if not config_path.exists():
        return NarrativeAcquisitionConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "narrative acquisition configuration must be a mapping"
        raise ValueError(msg)
    sources = []
    for raw_source in payload.get("sources", ()):
        if not isinstance(raw_source, dict):
            continue
        sources.append(
            NarrativeSourceConfig(
                source_id=str(raw_source.get("source_id") or raw_source.get("id") or ""),
                provider=str(raw_source.get("provider") or ""),
                project_id=str(raw_source.get("project_id") or raw_source.get("project") or ""),
                url=str(raw_source.get("url") or ""),
                source=str(raw_source.get("source") or raw_source.get("provider") or ""),
                language=str(raw_source.get("language") or "en"),
                author=str(raw_source.get("author") or ""),
                tags=_strings(raw_source.get("tags", ())),
                categories=_strings(raw_source.get("categories", ())),
                enabled=bool(raw_source.get("enabled", True)),
            )
        )
    return NarrativeAcquisitionConfig(
        enabled=bool(payload.get("enabled", True)),
        expired_after_days=int(payload.get("expired_after_days", 365)),
        sources=tuple(source for source in sources if source.source_id),
    )


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()
