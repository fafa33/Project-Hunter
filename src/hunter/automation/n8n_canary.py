"""Operational canary runner for the governed Smart Prompt Machine n8n boundary."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from hunter.automation.n8n_handoff import N8nPromptAutomationWorker, PromptAutomationHandoffError
from hunter.evidence_intelligence.smart_prompt_transport import PromptAutomationTransportError

N8N_CANARY_RECEIPT_SCHEMA_VERSION = "smart-prompt-n8n-canary-receipt-v1"


@dataclass(frozen=True, slots=True)
class N8nCanaryReceipt:
    """Persistable non-secret result of one accepted governed n8n canary."""

    dispatch_id: str
    payload_id: str
    receipt_id: str
    accepted: bool
    recorded_at: str
    schema_version: str = N8N_CANARY_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("dispatch_id", "payload_id", "receipt_id", "recorded_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.accepted is not True:
            raise ValueError("accepted canary receipts require accepted=true")
        if self.schema_version != N8N_CANARY_RECEIPT_SCHEMA_VERSION:
            raise ValueError("unknown n8n canary receipt schema version")

    def as_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(asdict(self))

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_n8n_canary_configuration(*, environ: Mapping[str, str] | None = None) -> None:
    """Validate worker-only operational configuration without performing network activity."""
    source = os.environ if environ is None else environ
    N8nPromptAutomationWorker.from_environment(environ=source, opener=_network_forbidden)


def run_n8n_canary(
    document: str | bytes,
    *,
    environ: Mapping[str, str] | None = None,
    opener: Any | None = None,
    recorded_at: datetime | None = None,
) -> N8nCanaryReceipt:
    """Dispatch one already-issued handoff and return only a non-secret accepted receipt."""
    source = os.environ if environ is None else environ
    worker = N8nPromptAutomationWorker.from_environment(environ=source, opener=opener)
    result = worker.dispatch_document(document)
    timestamp = recorded_at or datetime.now(UTC)
    acknowledgement = result.acknowledgement
    return N8nCanaryReceipt(
        dispatch_id=result.payload.dispatch_id,
        payload_id=result.payload.payload_id,
        receipt_id=acknowledgement.receipt_id,
        accepted=acknowledgement.accepted,
        recorded_at=timestamp.isoformat(),
    )


def write_n8n_canary_receipt(receipt: N8nCanaryReceipt, path: str | Path) -> None:
    """Write one canonical receipt document without operational secrets or prompt content."""
    if not isinstance(receipt, N8nCanaryReceipt):
        raise TypeError("receipt must be the canonical N8nCanaryReceipt")
    destination = Path(path)
    destination.write_text(receipt.to_json() + "\n", encoding="utf-8")


def _network_forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("configuration validation must not perform network activity")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter n8n-canary")
    parser.add_argument("handoff", nargs="?", help="path to an already-issued Smart Prompt Machine handoff JSON document")
    parser.add_argument("--validate-config", action="store_true", help="validate worker configuration without network activity")
    parser.add_argument("--receipt-out", help="write the accepted non-secret canary receipt to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if arguments.validate_config:
            if arguments.handoff is not None or arguments.receipt_out is not None:
                parser.error("--validate-config cannot be combined with a handoff or --receipt-out")
            validate_n8n_canary_configuration()
            print("n8n canary configuration valid")
            return 0
        if arguments.handoff is None:
            parser.error("handoff is required unless --validate-config is used")
        document = Path(arguments.handoff).read_bytes()
        receipt = run_n8n_canary(document)
        if arguments.receipt_out:
            write_n8n_canary_receipt(receipt, arguments.receipt_out)
        print(receipt.to_json())
        return 0
    except (OSError, PromptAutomationHandoffError, PromptAutomationTransportError, ValueError) as error:
        print(f"n8n canary failed: {error}")
        return 2


__all__ = [
    "N8N_CANARY_RECEIPT_SCHEMA_VERSION",
    "N8nCanaryReceipt",
    "main",
    "run_n8n_canary",
    "validate_n8n_canary_configuration",
    "write_n8n_canary_receipt",
]
