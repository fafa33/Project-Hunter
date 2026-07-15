from __future__ import annotations

from typing import Any

from hunter.execution.identity import identity


def sufficiency_id(kind: str, payload: Any) -> str:
    return identity(f"sufficiency-{kind}", payload)


def data_requirement_id(
    *,
    engine_id: str,
    analysis_purpose: str,
    output_field: str,
    requirement_kind: str,
    policy_id: str,
    policy_version: str,
    schema_version: str,
) -> str:
    return sufficiency_id(
        "requirement",
        {
            "engine_id": engine_id,
            "analysis_purpose": analysis_purpose,
            "output_field": output_field,
            "requirement_kind": requirement_kind,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "schema_version": schema_version,
        },
    )


def degraded_mode_policy_id(*, policy_name: str, policy_version: str, schema_version: str) -> str:
    return sufficiency_id(
        "degraded-mode-policy",
        {
            "policy_name": policy_name,
            "policy_version": policy_version,
            "schema_version": schema_version,
        },
    )


def proxy_signal_policy_id(
    *,
    proxy_type: str,
    policy_version: str,
    schema_version: str,
) -> str:
    return sufficiency_id(
        "proxy-signal-policy",
        {
            "proxy_type": proxy_type,
            "policy_version": policy_version,
            "schema_version": schema_version,
        },
    )
