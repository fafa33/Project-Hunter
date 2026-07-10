from __future__ import annotations

from hunter.intelligence.engines.protocol.models import (
    ApplicationSnapshot,
    FeeSnapshot,
    GovernanceSnapshot,
    IncentiveSnapshot,
    IncidentSnapshot,
    LiquiditySnapshot,
    ProtocolDataset,
    ProtocolEvent,
    ProtocolRecord,
    RevenueSnapshot,
    TransactionSnapshot,
    TreasurySnapshot,
    TVLSnapshot,
    UsageSnapshot,
    UserSnapshot,
    ValidatorSnapshot,
)


class ProtocolNormalizer:
    def normalize(self, records: tuple[ProtocolRecord, ...]) -> ProtocolDataset:
        deduplicated = _deduplicate(records)
        usage = tuple(record for record in deduplicated if isinstance(record, UsageSnapshot))
        users = tuple(record for record in deduplicated if isinstance(record, UserSnapshot))
        transactions = tuple(record for record in deduplicated if isinstance(record, TransactionSnapshot))
        fees = tuple(record for record in deduplicated if isinstance(record, FeeSnapshot))
        revenues = tuple(record for record in deduplicated if isinstance(record, RevenueSnapshot))
        tvl = tuple(record for record in deduplicated if isinstance(record, TVLSnapshot))
        liquidity = tuple(record for record in deduplicated if isinstance(record, LiquiditySnapshot))
        applications = tuple(record for record in deduplicated if isinstance(record, ApplicationSnapshot))
        validators = tuple(record for record in deduplicated if isinstance(record, ValidatorSnapshot))
        incidents = tuple(record for record in deduplicated if isinstance(record, IncidentSnapshot))
        governance = tuple(record for record in deduplicated if isinstance(record, GovernanceSnapshot))
        treasury = tuple(record for record in deduplicated if isinstance(record, TreasurySnapshot))
        incentives = tuple(record for record in deduplicated if isinstance(record, IncentiveSnapshot))
        events = tuple(record for record in deduplicated if isinstance(record, ProtocolEvent))
        missing = _missing(
            {
                "usage": usage,
                "users": users,
                "transactions": transactions,
                "fees": fees,
                "revenues": revenues,
                "tvl": tvl,
                "liquidity": liquidity,
                "applications": applications,
                "validators": validators,
                "incidents": incidents,
                "governance": governance,
                "treasury": treasury,
                "incentives": incentives,
                "events": events,
            }
        )
        project = sorted({record.project for record in deduplicated})[0] if deduplicated else "global-crypto"
        protocol = sorted({record.protocol for record in deduplicated})[0] if deduplicated else "unknown"
        return ProtocolDataset(
            project=project,
            protocol=protocol,
            records=deduplicated,
            usage=usage,
            users=users,
            transactions=transactions,
            fees=fees,
            revenues=revenues,
            tvl=tvl,
            liquidity=liquidity,
            applications=applications,
            validators=validators,
            incidents=incidents,
            governance=governance,
            treasury=treasury,
            incentives=incentives,
            events=events,
            missing_fields=missing,
            metadata={"record_count": str(len(deduplicated))},
        )


def _deduplicate(records: tuple[ProtocolRecord, ...]) -> tuple[ProtocolRecord, ...]:
    by_key: dict[tuple[str, str, str, str, str, str], ProtocolRecord] = {}
    for record in sorted(records, key=lambda item: (item.timestamp.isoformat(), item.id)):
        key = (
            type(record).__name__,
            record.project.lower(),
            record.protocol.lower(),
            (record.chain or "").lower(),
            (record.deployment or "").lower(),
            record.timestamp.isoformat(),
        )
        existing = by_key.get(key)
        if existing is None or record.reliability > existing.reliability:
            by_key[key] = record
    return tuple(sorted(by_key.values(), key=lambda item: (item.timestamp.isoformat(), item.id)))


def _missing(groups: dict[str, tuple[object, ...]]) -> tuple[str, ...]:
    return tuple(sorted(name for name, values in groups.items() if not values))
