from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from hunter.execution.exceptions import CanonicalizationError


class _Missing:
    pass


MISSING = _Missing()


def canonicalize(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes for supported analytical identity inputs."""

    normalized = normalize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def normalize(value: Any) -> Any:
    if value is MISSING:
        return {"__type__": "missing"}
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return {"__type__": "int", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("Non-finite floats cannot be canonicalized")
        return {"__type__": "decimal", "value": _decimal_text(Decimal(str(value)))}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalizationError("Non-finite decimals cannot be canonicalized")
        return {"__type__": "decimal", "value": _decimal_text(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": _datetime_text(value)}
    if isinstance(value, Enum):
        return {"__type__": "enum", "class": value.__class__.__name__, "value": normalize(value.value)}
    if isinstance(value, Path):
        return {"__type__": "path", "value": value.as_posix()}
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            normalized_key = normalize(key)
            if isinstance(normalized_key, dict):
                key_sort = json.dumps(normalized_key, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            elif isinstance(normalized_key, str):
                key_sort = normalized_key
            else:
                key_sort = json.dumps(normalized_key, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            items.append((key_sort, normalized_key, normalize(item)))
        return {"__type__": "mapping", "items": [(key, item) for _, key, item in sorted(items, key=lambda row: row[0])]}
    if isinstance(value, list | tuple):
        return {"__type__": "sequence", "items": [normalize(item) for item in value]}
    if isinstance(value, set | frozenset):
        items = [normalize(item) for item in value]
        return {
            "__type__": "set",
            "items": sorted(
                items,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            ),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": "dataclass",
            "class": value.__class__.__name__,
            "fields": {field.name: normalize(getattr(value, field.name)) for field in fields(value)},
        }
    raise CanonicalizationError(f"Unsupported canonicalization value: {type(value).__name__}")


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise CanonicalizationError("Naive datetimes cannot be canonicalized")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return format(normalized, "f")
    return format(normalized, "f").rstrip("0").rstrip(".")

