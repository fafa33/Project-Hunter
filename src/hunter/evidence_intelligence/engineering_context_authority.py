"""Repository-owned engineering context authority for implementation tasks.

The authority turns canonical DPM registry knowledge into deterministic,
bounded context before Smart Prompt Machine compilation. It never consumes
Issue/task prose when deciding which defect families apply.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ENGINEERING_IMPLEMENT_TASK_KEY = "engineering.implement"

DEFAULT_DEFECT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "docs" / "DEFECT_REGISTRY.json"
ENGINEERING_IMPLEMENT_ROUTE_SURFACES = (
    ".github/",
    ".githooks/",
    ".hunter/",
    "build_backend/",
    "docs/",
    "pyproject.toml",
    "railway.toml",
    "scripts/",
    "src/",
    "tests/",
)
ENGINEERING_CONTEXT_SCHEMA_VERSION = "engineering-context-authority-v1"
CANONICAL_DEFECT_LIFECYCLES = frozenset(
    {"recorded", "regression-tested", "locally-enforced", "hosted-enforced", "merge-enforced", "prevented"}
)
CANONICAL_PREVENTION_BOUNDARIES = frozenset({"review", "local-pre-push", "hosted-gate", "merge-gate"})


class EngineeringContextAuthorityError(RuntimeError):
    """Raised when canonical engineering context cannot be proven safely."""


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineeringContextAuthorityError(f"{name} must be non-empty text")
    return value.strip()


def _path_intersects(left: str, right: str) -> bool:
    left = left.rstrip("/")
    right = right.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


class EngineeringContextAuthority:
    """Compile machine-owned prevention context for a governed engineering route."""

    def __init__(self, *, registry_path: Path = DEFAULT_DEFECT_REGISTRY_PATH) -> None:
        self._registry_path = Path(registry_path)

    def _families(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EngineeringContextAuthorityError("defect registry is unavailable or malformed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("families"), list):
            raise EngineeringContextAuthorityError("defect registry families must be a list")
        families: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in payload["families"]:
            if not isinstance(raw, dict):
                raise EngineeringContextAuthorityError("defect family must be an object")
            identifier = _required_text("defect family id", raw.get("id"))
            if identifier in seen:
                raise EngineeringContextAuthorityError(f"duplicate defect family: {identifier}")
            seen.add(identifier)
            applicability = raw.get("applicability")
            if not isinstance(applicability, dict):
                raise EngineeringContextAuthorityError(f"{identifier} applicability must be an object")
            changed_paths = applicability.get("changed_paths")
            if not isinstance(changed_paths, list) or not changed_paths:
                raise EngineeringContextAuthorityError(f"{identifier} changed_paths must be a non-empty list")
            if any(not isinstance(path, str) or not path.strip() for path in changed_paths):
                raise EngineeringContextAuthorityError(f"{identifier} changed_paths must contain non-empty text")
            prevention = raw.get("prevention")
            if not isinstance(prevention, dict):
                raise EngineeringContextAuthorityError(f"{identifier} prevention must be an object")
            boundary = _required_text(f"{identifier} prevention boundary", prevention.get("boundary"))
            if boundary not in CANONICAL_PREVENTION_BOUNDARIES:
                raise EngineeringContextAuthorityError(
                    f"{identifier} prevention boundary must be canonical: {boundary!r}"
                )
            _required_text(f"{identifier} title", raw.get("title"))
            _required_text(f"{identifier} invariant", raw.get("invariant"))
            lifecycle = _required_text(f"{identifier} lifecycle", raw.get("lifecycle"))
            if lifecycle not in CANONICAL_DEFECT_LIFECYCLES:
                raise EngineeringContextAuthorityError(f"{identifier} lifecycle must be canonical: {lifecycle!r}")
            families.append(raw)
        return families

    def compile(self, task_key: str) -> dict[str, object]:
        if task_key != ENGINEERING_IMPLEMENT_TASK_KEY:
            raise EngineeringContextAuthorityError(f"unsupported engineering context route: {task_key}")
        selected: list[dict[str, str]] = []
        for family in self._families():
            paths = family["applicability"]["changed_paths"]
            if not any(
                _path_intersects(path, surface) for path in paths for surface in ENGINEERING_IMPLEMENT_ROUTE_SURFACES
            ):
                continue
            prevention = family["prevention"]
            item = {
                "id": family["id"],
                "title": family["title"],
                "invariant": family["invariant"],
                "lifecycle": family["lifecycle"],
                "prevention_boundary": prevention["boundary"],
            }
            guard_reference = prevention.get("guard_reference")
            if guard_reference is not None:
                item["guard_reference"] = _required_text(f"{family['id']} guard_reference", guard_reference)
            selected.append(item)
        selected.sort(key=lambda item: item["id"])
        if not selected:
            raise EngineeringContextAuthorityError("no applicable defect families for engineering implementation route")
        return {
            "schema_version": ENGINEERING_CONTEXT_SCHEMA_VERSION,
            "task_key": task_key,
            "applicable_defect_families": selected,
        }

    def canonical_json(self, task_key: str) -> str:
        return json.dumps(self.compile(task_key), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
