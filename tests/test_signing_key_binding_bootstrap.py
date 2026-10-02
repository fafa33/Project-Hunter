"""Trusted-main signing-key binding bootstrap (Issue #535 / PR #538).

Main carries only the data; enforcement is owned by PR #538, which reads it from
the trusted base tip. These tests pin the data and the structural invariants a
binding must keep, with paired negative/positive fixtures.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = re.compile(r"\ASHA256:[A-Za-z0-9+/]{43}\Z")
CLAUDE_KEY = "SHA256:32dP45eSMmVSt/G/CGvcxl/P+MO3Nwj9xeTh/GSA2wc"
OWNER_KEY = "SHA256:Yee9tbonym7Jvs2UbjpudVIRkv3aF1tcFlK240pXuBo"
EXPECTED = {"claude": [CLAUDE_KEY], "fafa33": [OWNER_KEY]}


def _binding() -> dict[str, Any]:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    return policy["writer_identity_binding"]


def _problems(binding: dict[str, Any]) -> list[str]:
    section = binding.get("signing_key_bindings")
    if not isinstance(section, dict) or not isinstance(section.get("bindings"), dict) or not section["bindings"]:
        return ["signing_key_bindings.bindings must be a non-empty object"]
    if section.get("require_key_bound_to_resolved_writer") is not True:
        return ["require_key_bound_to_resolved_writer must be true, or the bindings are never enforced"]
    logins = {identity["login"] for identity in binding["identities"]}
    problems: list[str] = []
    owner: dict[str, str] = {}
    for login, keys in section["bindings"].items():
        if login not in logins:
            problems.append(f"{login!r} is not a bound writer")
        if not isinstance(keys, list) or not keys:
            problems.append(f"{login!r} binds no key")
            continue
        for key in keys:
            if not isinstance(key, str) or not FINGERPRINT.match(key):
                problems.append(f"{login!r} binds a non-fingerprint {key!r}")
            elif owner.setdefault(key, login) != login:
                problems.append(f"{key} is bound to two writers")
    problems.extend(f"{login!r} has no bound key" for login in sorted(logins - set(section["bindings"])))
    return problems


def test_trusted_main_binds_exactly_the_governed_owner_and_claude_keys() -> None:
    binding = _binding()
    assert binding["signing_key_bindings"]["bindings"] == EXPECTED
    assert _problems(binding) == []


def _mutated(mutate) -> dict[str, Any]:
    binding = copy.deepcopy(_binding())
    mutate(binding["signing_key_bindings"]["bindings"])
    return binding


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(intruder=["SHA256:" + "A" * 43]),  # arbitrary self-binding for an unbound writer
        lambda b: b.update(claude=[OWNER_KEY]),  # one key bound to two writers
        lambda b: b.update(claude=["*"]),  # wildcard
        lambda b: b.update(claude=["SHA256:short"]),  # partial fingerprint
        lambda b: b.update(claude=[]),  # empty binding
        lambda b: b.pop("fafa33"),  # writer left without a key
    ],
)
def test_structurally_invalid_bindings_are_rejected(mutate) -> None:
    assert _problems(_mutated(mutate)) != []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(claude=["SHA256:" + "B" * 43]),  # unknown replacement key
        lambda b: b["claude"].append("SHA256:" + "C" * 43),  # candidate-added extra key
    ],
)
def test_well_formed_but_unknown_keys_are_not_the_governed_binding(mutate) -> None:
    candidate = _mutated(mutate)
    assert _problems(candidate) == []
    assert candidate["signing_key_bindings"]["bindings"] != EXPECTED


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_bindings_without_the_enforcement_flag_are_rejected(value) -> None:
    candidate = copy.deepcopy(_binding())
    section = candidate["signing_key_bindings"]
    if value is None:
        section.pop("require_key_bound_to_resolved_writer")
    else:
        section["require_key_bound_to_resolved_writer"] = value
    assert _problems(candidate) != []


def test_the_trusted_binding_enables_enforcement() -> None:
    assert _binding()["signing_key_bindings"]["require_key_bound_to_resolved_writer"] is True
