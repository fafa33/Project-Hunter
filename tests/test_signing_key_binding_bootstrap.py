"""Trusted signing-key bindings bootstrap (Issue #412, DFF-010).

The writer binding's signing-key section is root-of-trust data: it decides
which SSH key may sign for which authorization-bound writer. It must therefore
be established on the trusted default branch by an owner-merged change, never
by the candidate that will be judged against it. These tests pin the exact
bindings that trusted evidence supports, so any change to them -- a
self-binding, a replacement or an extra key -- is a deliberate, reviewed edit
of this file rather than a silent policy drift.

Evidence for each pinned fingerprint:

* ``claude``: the ``%GK`` fingerprint of every SSH-signed commit reachable
  from main at 207475240223027ce5875c8cdbc30f4f288baafb, all authored and
  committed as ``Claude <noreply@anthropic.com>`` and admitted through governed
  ingress that requires GitHub ``verified=true reason=valid``.
* ``fafa33``: the SSH signing key registered on the GitHub account ``fafa33``
  ("Mac Git Signing", key id 1160850), identical to the owner's configured git
  signing key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "CODE_WRITE_POLICY.json"
FINGERPRINT = re.compile(r"\ASHA256:[A-Za-z0-9+/]{43}\Z")

TRUSTED_BINDINGS = {
    "claude": ["SHA256:32dP45eSMmVSt/G/CGvcxl/P+MO3Nwj9xeTh/GSA2wc"],
    "fafa33": ["SHA256:Yee9tbonym7Jvs2UbjpudVIRkv3aF1tcFlK240pXuBo"],
}


def _binding() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))["writer_identity_binding"]


def _section() -> dict:
    return _binding()["signing_key_bindings"]


def test_the_trusted_bindings_are_exactly_the_evidenced_keys() -> None:
    """Any self-binding, replacement or extra key changes this and fails."""
    assert _section()["bindings"] == TRUSTED_BINDINGS


def test_key_binding_is_required_for_every_resolved_writer() -> None:
    assert _section()["require_key_bound_to_resolved_writer"] is True


def test_every_bound_writer_has_exactly_its_own_explicit_key() -> None:
    """No wildcard, no partial fingerprint, no key shared between writers, no unbound login."""
    bindings = _section()["bindings"]
    logins = {identity["login"] for identity in _binding()["identities"]}

    assert set(bindings) == logins
    seen: dict[str, str] = {}
    for login, keys in bindings.items():
        assert isinstance(keys, list) and keys, login
        for key in keys:
            assert FINGERPRINT.match(key), f"{login}: {key!r} is not a full SSH SHA-256 fingerprint"
            assert key not in seen, f"{key} is bound to both {seen.get(key)!r} and {login!r}"
            seen[key] = login


def test_every_binding_records_its_trusted_evidence() -> None:
    evidence = _section()["binding_evidence"]

    assert set(evidence) == set(TRUSTED_BINDINGS)
    for login, keys in TRUSTED_BINDINGS.items():
        assert all(key in evidence[login] for key in keys), login
