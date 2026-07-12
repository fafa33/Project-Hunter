from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.execution.identity import identity
from hunter.historical.models import HistoricalValidationCase
from hunter.historical_acquisition.models import HistoricalProviderMetadata, RawHistoricalEvidence


class HistoricalProvider(Protocol):
    metadata: HistoricalProviderMetadata

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]: ...


class CoinGeckoHistoricalProvider:
    metadata = HistoricalProviderMetadata(
        name="coingecko-historical",
        collector="coingecko-history",
        supported_metrics=(
            "price",
            "market_cap",
            "volume",
            "supply",
            "fdv",
            "historical_rankings",
            "historical_categories",
        ),
    )

    def __init__(
        self,
        *,
        id_map: dict[str, str],
        base_url: str = "https://api.coingecko.com/api/v3",
        months_before: int = 0,
        months_after: int = 0,
        extra_offsets_days: tuple[int, ...] = (),
    ) -> None:
        self.id_map = id_map
        self.base_url = base_url.rstrip("/")
        self.months_before = months_before
        self.months_after = months_after
        self.extra_offsets_days = extra_offsets_days

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        rows = []
        for case in cases:
            coin_id = self.id_map.get(case.project_id)
            if not coin_id:
                continue
            dates = _historical_timestamps(
                case,
                months_before=self.months_before,
                months_after=self.months_after,
                extra_offsets_days=self.extra_offsets_days,
            )
            range_rows = self._range_rows(case, coin_id, dates)
            if range_rows:
                rows.extend(range_rows)
                continue
            for observed_at in dates:
                date = observed_at.strftime("%d-%m-%Y")
                url = f"{self.base_url}/coins/{urllib.parse.quote(coin_id)}/history?date={date}&localization=false"
                payload = _get_json(url)
                if not payload or "error" in payload:
                    continue
                market = payload.get("market_data") if isinstance(payload.get("market_data"), dict) else {}
                price = _usd(market.get("current_price"))
                market_cap = _usd(market.get("market_cap"))
                volume = _usd(market.get("total_volume"))
                if price is None and market_cap is None and volume is None:
                    continue
                raw_payload = {
                    "coingecko_id": coin_id,
                    "price": price,
                    "market_cap": market_cap,
                    "volume": volume,
                    "symbol": payload.get("symbol"),
                    "name": payload.get("name"),
                    "categories": payload.get("categories", ()),
                }
                rows.append(_raw(self.metadata, case, "historical_market", raw_payload, url, observed_at=observed_at))
            time.sleep(1.3)
        return tuple(rows)

    def _range_rows(
        self,
        case: HistoricalValidationCase,
        coin_id: str,
        dates: tuple[datetime, ...],
    ) -> tuple[RawHistoricalEvidence, ...]:
        if len(dates) <= 1:
            return ()
        start = min(dates) - timedelta(days=2)
        end = max(dates) + timedelta(days=2)
        url = (
            f"{self.base_url}/coins/{urllib.parse.quote(coin_id)}/market_chart/range?"
            f"vs_currency=usd&from={int(start.timestamp())}&to={int(end.timestamp())}"
        )
        payload = _get_json(url)
        if not isinstance(payload, dict) or "error" in payload:
            return ()
        points = _market_chart_points(payload)
        rows = []
        for observed_at in dates:
            closest = _closest_market_point(points, observed_at)
            if closest is None:
                continue
            timestamp, values = closest
            raw_payload = {
                "coingecko_id": coin_id,
                "price": values.get("price"),
                "market_cap": values.get("market_cap"),
                "volume": values.get("volume"),
            }
            rows.append(_raw(self.metadata, case, "historical_market", raw_payload, url, observed_at=timestamp))
        return tuple(rows)


class DefiLlamaHistoricalProvider:
    metadata = HistoricalProviderMetadata(
        name="defillama-historical",
        collector="defillama-history",
        supported_metrics=("tvl", "fees", "revenue", "protocol_activity"),
    )

    def __init__(
        self,
        *,
        slug_map: dict[str, str],
        base_url: str = "https://api.llama.fi",
        months_before: int = 0,
        months_after: int = 0,
        extra_offsets_days: tuple[int, ...] = (),
    ) -> None:
        self.slug_map = slug_map
        self.base_url = base_url.rstrip("/")
        self.months_before = months_before
        self.months_after = months_after
        self.extra_offsets_days = extra_offsets_days

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        rows = []
        for case in cases:
            slug = self.slug_map.get(case.project_id)
            if not slug:
                continue
            url = f"{self.base_url}/protocol/{urllib.parse.quote(slug)}"
            payload = _get_json(url)
            if not payload:
                continue
            tvl_rows = payload.get("tvl") if isinstance(payload.get("tvl"), list) else []
            fees_url = f"{self.base_url}/summary/fees/{urllib.parse.quote(slug)}"
            fees_payload = _get_json(fees_url)
            fees_chart = _fees_chart(fees_payload, "totalDataChart")
            revenue_url = f"{fees_url}?dataType=dailyRevenue"
            revenue_payload = _get_json(revenue_url)
            revenue_chart = _fees_chart(revenue_payload, "totalDataChart")
            for observed_at in _historical_timestamps(
                case,
                months_before=self.months_before,
                months_after=self.months_after,
                extra_offsets_days=self.extra_offsets_days,
            ):
                closest = _closest_tvl(tvl_rows, observed_at)
                if not closest:
                    continue
                fees_point = _closest_chart_point(fees_chart, observed_at)
                revenue_point = _closest_chart_point(revenue_chart, observed_at)
                rows.append(
                    _raw(
                        self.metadata,
                        case,
                        "historical_protocol",
                        {
                            "defillama_slug": slug,
                            "tvl": closest.get("totalLiquidityUSD"),
                            "category": payload.get("category"),
                            "chains": payload.get("chains", ()),
                            "parent_protocol": payload.get("parentProtocol"),
                            "daily_fees_usd": fees_point,
                            "daily_revenue_usd": revenue_point,
                        },
                        url,
                        observed_at=_tvl_timestamp(closest) or observed_at,
                    )
                )
            time.sleep(1.0)
        return tuple(rows)


class GitHubHistoricalActivityProvider:
    metadata = HistoricalProviderMetadata(
        name="github-historical",
        collector="github-history",
        supported_metrics=("commits", "contributors", "releases", "developer_activity"),
    )

    def __init__(
        self,
        *,
        repository_map: dict[str, tuple[str, ...]],
        base_url: str = "https://api.github.com",
        months_before: int = 0,
        months_after: int = 0,
        extra_offsets_days: tuple[int, ...] = (),
    ) -> None:
        self.repository_map = repository_map
        self.base_url = base_url.rstrip("/")
        self.months_before = months_before
        self.months_after = months_after
        self.extra_offsets_days = extra_offsets_days

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        rows = []
        for case in cases:
            repositories = self.repository_map.get(case.project_id, ())
            for repository in repositories[:1]:
                encoded = urllib.parse.quote(repository)
                for observed_at in _historical_timestamps(
                    case,
                    months_before=self.months_before,
                    months_after=self.months_after,
                    extra_offsets_days=self.extra_offsets_days,
                ):
                    since = urllib.parse.quote((observed_at - timedelta(days=30)).isoformat())
                    until = urllib.parse.quote(observed_at.isoformat())
                    url = f"{self.base_url}/repos/{encoded}/commits?since={since}&until={until}&per_page=100"
                    payload = _get_json(url)
                    if payload is None:
                        continue
                    rows.append(
                        _raw(
                            self.metadata,
                            case,
                            "historical_developer",
                            {
                                "repository": repository,
                                "commits": len(payload) if isinstance(payload, list) else 0,
                                "active_window_days": 30,
                            },
                            url,
                            observed_at=observed_at,
                        )
                    )
                release_rows = self._release_rows(case, repository, encoded)
                rows.extend(release_rows)
                time.sleep(1.0)
        return tuple(rows)

    def _release_rows(
        self,
        case: HistoricalValidationCase,
        repository: str,
        encoded_repository: str,
    ) -> tuple[RawHistoricalEvidence, ...]:
        url = f"{self.base_url}/repos/{encoded_repository}/releases?per_page=100"
        payload = _get_json(url)
        if not isinstance(payload, list):
            return ()
        rows = []
        cutoff = max(
            _historical_timestamps(
                case,
                months_before=self.months_before,
                months_after=self.months_after,
                extra_offsets_days=self.extra_offsets_days,
            )
        )
        for release in payload:
            if not isinstance(release, dict):
                continue
            published = _timestamp(release.get("published_at"))
            if published is None or published > cutoff:
                continue
            rows.append(
                _raw(
                    self.metadata,
                    case,
                    "historical_developer",
                    {
                        "repository": repository,
                        "releases": 1,
                        "tag_name": release.get("tag_name"),
                        "release_name": release.get("name"),
                    },
                    url,
                    observed_at=published,
                )
            )
        return tuple(rows[:24])


class HistoricalRSSAnnouncementsProvider:
    metadata = HistoricalProviderMetadata(
        name="historical-rss-announcements",
        collector="rss-history",
        supported_metrics=("historical_narrative", "announcements"),
    )

    def __init__(self, repository: FileAcquisitionRepository | None = None) -> None:
        self.repository = repository or FileAcquisitionRepository()

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        validations = self.repository.validations
        rows: list[RawHistoricalEvidence] = []
        for case in cases:
            for evidence in self.repository.normalized.values():
                if evidence.provider != "narrative" or evidence.target_id != case.project_id:
                    continue
                validation = validations.get(evidence.evidence_id)
                if validation is None or validation.status != "valid":
                    continue
                timestamp = _timestamp(evidence.raw_metrics.get("timestamp"))
                if timestamp is None or timestamp > case.evaluation_timestamp:
                    continue
                payload = {
                    "title": evidence.raw_metrics.get("title"),
                    "description": evidence.raw_metrics.get("description"),
                    "url": evidence.source_url,
                    "topics": evidence.raw_metrics.get("topics", ()),
                    "entities": evidence.raw_metrics.get("entities", ()),
                    "narratives": evidence.raw_metrics.get("narratives", ()),
                    "keywords": evidence.raw_metrics.get("keywords", ()),
                }
                rows.append(
                    RawHistoricalEvidence(
                        provider=self.metadata.name,
                        collector=self.metadata.collector,
                        raw_source_id=identity(
                            "historical-rss-raw",
                            {"case": case.case_id, "evidence": evidence.evidence_id},
                        ),
                        case_id=case.case_id,
                        project_id=case.project_id,
                        metric="historical_narrative",
                        event_timestamp=timestamp,
                        publication_timestamp=timestamp,
                        data_availability_timestamp=timestamp,
                        retrieval_timestamp=evidence.retrieved_at,
                        payload=payload,
                        source_url=evidence.source_url,
                        repository_id=f"historical-rss:{case.case_id}:{evidence.evidence_id}",
                    )
                )
        return tuple(rows)


class InternetArchiveSnapshotProvider:
    """Wayback Machine (web.archive.org) CDX API - real archived snapshots of a project's primary domain."""

    metadata = HistoricalProviderMetadata(
        name="internet-archive-historical",
        collector="wayback-cdx",
        supported_metrics=("historical_archive_presence",),
    )

    def __init__(
        self,
        *,
        domain_map: dict[str, str],
        base_url: str = "http://web.archive.org/cdx/search/cdx",
        months_before: int = 0,
        months_after: int = 0,
        extra_offsets_days: tuple[int, ...] = (),
    ) -> None:
        self.domain_map = domain_map
        self.base_url = base_url.rstrip("/")
        self.months_before = months_before
        self.months_after = months_after
        self.extra_offsets_days = extra_offsets_days

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        rows = []
        for case in cases:
            domain = self.domain_map.get(case.project_id)
            if not domain:
                continue
            dates = _historical_timestamps(
                case,
                months_before=self.months_before,
                months_after=self.months_after,
                extra_offsets_days=self.extra_offsets_days,
            )
            if not dates:
                continue
            start = min(dates) - timedelta(days=30)
            end = max(dates) + timedelta(days=2)
            url = (
                f"{self.base_url}?url={urllib.parse.quote(domain)}&output=json"
                f"&from={start.strftime('%Y%m%d')}&to={end.strftime('%Y%m%d')}&filter=statuscode:200&collapse=timestamp:8"
            )
            payload = _get_json(url)
            snapshots = _wayback_snapshots(payload)
            for observed_at in dates:
                closest = _closest_wayback_snapshot(snapshots, observed_at)
                if closest is None:
                    continue
                snapshot_ts, digest, original = closest
                archive_url = f"http://web.archive.org/web/{snapshot_ts.strftime('%Y%m%d%H%M%S')}/{original}"
                rows.append(
                    _raw(
                        self.metadata,
                        case,
                        "historical_archive_presence",
                        {
                            "domain": domain,
                            "archived_url": archive_url,
                            "content_digest": digest,
                            "days_from_target": abs((snapshot_ts - observed_at).days),
                        },
                        archive_url,
                        observed_at=snapshot_ts,
                    )
                )
            time.sleep(1.0)
        return tuple(rows)


class GovernanceArchiveProvider:
    """Snapshot.org (hub.snapshot.org) GraphQL API - real off-chain governance proposal history."""

    metadata = HistoricalProviderMetadata(
        name="governance-archive-snapshot",
        collector="snapshot-graphql",
        supported_metrics=("historical_governance",),
    )

    def __init__(
        self,
        *,
        space_map: dict[str, str],
        base_url: str = "https://hub.snapshot.org/graphql",
        limit: int = 50,
    ) -> None:
        self.space_map = space_map
        self.base_url = base_url
        self.limit = limit

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        rows = []
        for case in cases:
            space = self.space_map.get(case.project_id)
            if not space:
                continue
            query = (
                f'query {{ proposals(first: {self.limit}, orderBy: "created", orderDirection: desc, '
                f'where: {{space_in: ["{space}"]}}) {{ id title state created author scores_total votes }} }}'
            )
            payload = _post_json(self.base_url, {"query": query})
            proposals = _snapshot_proposals(payload)
            for proposal in proposals:
                created = _epoch_timestamp(proposal.get("created"))
                if created is None or created > case.evaluation_timestamp:
                    continue
                proposal_id = str(proposal.get("id", ""))
                proposal_url = f"https://snapshot.org/#/{space}/proposal/{proposal_id}"
                rows.append(
                    _raw(
                        self.metadata,
                        case,
                        "historical_governance",
                        {
                            "space": space,
                            "proposal_id": proposal_id,
                            "title": proposal.get("title"),
                            "state": proposal.get("state"),
                            "author": proposal.get("author"),
                            "votes": proposal.get("votes"),
                            "scores_total": proposal.get("scores_total"),
                        },
                        proposal_url,
                        observed_at=created,
                    )
                )
            time.sleep(1.0)
        return tuple(rows)


def future_provider_metadata() -> tuple[HistoricalProviderMetadata, ...]:
    return tuple(
        HistoricalProviderMetadata(name=name, collector="future", supported_metrics=())
        for name in (
            "cryptocompare",
            "coinmarketcap",
            "messari",
            "artemis",
            "token-terminal",
            "dune",
            "the-graph",
            "token-unlock-schedules",
        )
    )


def _raw(
    metadata: HistoricalProviderMetadata,
    case: HistoricalValidationCase,
    metric: str,
    payload: dict[str, object],
    url: str,
    *,
    observed_at: datetime | None = None,
) -> RawHistoricalEvidence:
    timestamp = (observed_at or case.evaluation_timestamp).astimezone(UTC)
    repository_id = f"{metadata.name}:{case.case_id}:{metric}:{timestamp.date().isoformat()}"
    return RawHistoricalEvidence(
        provider=metadata.name,
        collector=metadata.collector,
        raw_source_id=identity(
            "historical-raw",
            {"repository_id": repository_id, "timestamp": timestamp.isoformat(), "payload": payload},
        ),
        case_id=case.case_id,
        project_id=case.project_id,
        metric=metric,
        event_timestamp=timestamp,
        publication_timestamp=timestamp,
        data_availability_timestamp=timestamp,
        retrieval_timestamp=datetime.now(tz=UTC),
        payload=payload,
        source_url=url,
        repository_id=repository_id,
    )


def _get_json(url: str) -> Any | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _post_json(url: str, body: dict[str, object]) -> Any | None:
    try:
        request = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _wayback_snapshots(payload: object) -> tuple[tuple[datetime, str, str], ...]:
    if not isinstance(payload, list) or len(payload) < 2:
        return ()
    header = payload[0]
    if not isinstance(header, list) or "timestamp" not in header:
        return ()
    ts_index = header.index("timestamp")
    digest_index = header.index("digest") if "digest" in header else None
    original_index = header.index("original") if "original" in header else None
    rows = []
    for row in payload[1:]:
        if not isinstance(row, list) or len(row) <= ts_index:
            continue
        try:
            timestamp = datetime.strptime(row[ts_index], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except (ValueError, IndexError):
            continue
        digest = row[digest_index] if digest_index is not None and len(row) > digest_index else ""
        original = row[original_index] if original_index is not None and len(row) > original_index else ""
        rows.append((timestamp, str(digest), str(original)))
    return tuple(rows)


def _closest_wayback_snapshot(
    snapshots: tuple[tuple[datetime, str, str], ...],
    target: datetime,
) -> tuple[datetime, str, str] | None:
    if not snapshots:
        return None
    return min(snapshots, key=lambda item: abs((item[0] - target).total_seconds()))


def _snapshot_proposals(payload: object) -> tuple[dict[str, object], ...]:
    if not isinstance(payload, dict):
        return ()
    data = payload.get("data")
    if not isinstance(data, dict):
        return ()
    proposals = data.get("proposals")
    if not isinstance(proposals, list):
        return ()
    return tuple(item for item in proposals if isinstance(item, dict))


def _fees_chart(payload: object, key: str) -> tuple[tuple[datetime, float], ...]:
    if not isinstance(payload, dict):
        return ()
    chart = payload.get(key)
    if not isinstance(chart, list):
        return ()
    points = []
    for item in chart:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        if not isinstance(item[0], int | float) or not isinstance(item[1], int | float):
            continue
        points.append((datetime.fromtimestamp(float(item[0]), tz=UTC), float(item[1])))
    return tuple(points)


def _closest_chart_point(points: tuple[tuple[datetime, float], ...], target: datetime) -> float | None:
    candidates = tuple(item for item in points if item[0] <= target)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _epoch_timestamp(value: object) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None


def _usd(value: object) -> float | None:
    if isinstance(value, dict) and isinstance(value.get("usd"), int | float):
        return float(value["usd"])
    return None


def _closest_tvl(rows: Iterable[object], cutoff: datetime) -> dict[str, object]:
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), int | float):
            continue
        timestamp = datetime.fromtimestamp(float(row["date"]), tz=UTC)
        if timestamp <= cutoff:
            candidates.append((timestamp, row))
    if not candidates:
        return {}
    return dict(max(candidates, key=lambda item: item[0])[1])


def _tvl_timestamp(row: dict[str, object]) -> datetime | None:
    if isinstance(row.get("date"), int | float):
        return datetime.fromtimestamp(float(row["date"]), tz=UTC)
    return None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _monthly_timestamps(
    case: HistoricalValidationCase,
    *,
    months_before: int,
    months_after: int,
) -> tuple[datetime, ...]:
    offsets = range(-months_before, months_after + 1)
    now = datetime.now(tz=UTC)
    timestamps = []
    for offset in offsets:
        candidate = _shift_month(case.evaluation_timestamp, offset)
        if candidate <= now:
            timestamps.append(candidate)
    return tuple(sorted(set(timestamps)))


def _historical_timestamps(
    case: HistoricalValidationCase,
    *,
    months_before: int,
    months_after: int,
    extra_offsets_days: tuple[int, ...],
) -> tuple[datetime, ...]:
    now = datetime.now(tz=UTC)
    timestamps = list(_monthly_timestamps(case, months_before=months_before, months_after=months_after))
    for days in extra_offsets_days:
        candidate = case.evaluation_timestamp + timedelta(days=days)
        if candidate <= now:
            timestamps.append(candidate)
    return tuple(sorted(set(timestamps)))


def _shift_month(value: datetime, months: int) -> datetime:
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = (month_index % 12) + 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        following = datetime(year, month + 1, 1, tzinfo=UTC)
    current = datetime(year, month, 1, tzinfo=UTC)
    return (following - current).days


def _market_chart_points(payload: dict[str, object]) -> dict[datetime, dict[str, float]]:
    rows: dict[datetime, dict[str, float]] = {}
    for key, metric in (("prices", "price"), ("market_caps", "market_cap"), ("total_volumes", "volume")):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, list | tuple) or len(item) < 2:
                continue
            if not isinstance(item[0], int | float) or not isinstance(item[1], int | float):
                continue
            timestamp = datetime.fromtimestamp(float(item[0]) / 1000.0, tz=UTC)
            rows.setdefault(timestamp, {})[metric] = float(item[1])
    return rows


def _closest_market_point(
    points: dict[datetime, dict[str, float]],
    target: datetime,
) -> tuple[datetime, dict[str, float]] | None:
    candidates = tuple((timestamp, values) for timestamp, values in points.items() if timestamp <= target)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])
