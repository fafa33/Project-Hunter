#!/usr/bin/env python3
"""Bootstrap the production Source Handling authority root and genesis rule.

This is the operator-only production bootstrap path for the Railway issuer
edge: it provisions ``source_handling_operator_root`` and publishes the
repository-owned genesis ``AUTHORIZATION_RULE`` into the exact evidence database
the issuer will read at runtime, without any hand-written SQL and without any
test fixture dependency.

The genesis rule is always the repository-owned
``config/source_handling/authorization_rule_v1.json``, pinned to canonical
digest ``41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a``;
the CLI accepts no alternate rule path. A missing, malformed, or
digest-mismatched production rule fails closed before any authority state is
written.

The Source Handling private signing key is consumed only here (from the
``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` environment variable or a
``--signing-key-file``, mutually exclusive sources); it is derived into the
three non-secret operator outputs the issuer runtime requires, and it is never
printed. The issuer runtime never holds this key.

The command is idempotent only when the existing operator root and genesis
exactly match the derived material; any mismatch (foreign root, mismatched
signing key, mismatched genesis, or existing authority history) fails closed and
never replaces authority state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.source_handling_persistence import (
    SOURCE_HANDLING_RULE_SCOPE,
    SourceHandlingAuthorityService,
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
    _canonical_json,
    _plain_mapping,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    production_provenance_resolver,
)

SIGNING_KEY_ENV = "HUNTER_SOURCE_HANDLING_SIGNING_KEY"
VERIFICATION_KEY_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY"
VERIFICATION_KEY_SHA256_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256"
GENESIS_RULE_SHA256_ENV = "HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256"

PINNED_PRODUCTION_RULE_SHA256 = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RULE = _REPO_ROOT / "config" / "source_handling" / "authorization_rule_v1.json"


def _load_signing_key(*, environ: Mapping[str, str], signing_key_file: str | None) -> bytes:
    """Return the 32-byte Ed25519 private key without echoing it.

    Exactly one source may be supplied: the ``HUNTER_SOURCE_HANDLING_SIGNING_KEY``
    environment variable or ``--signing-key-file``. Providing both is an explicit
    deterministic error before any database is opened, so a silently preferred
    source can never mask a misconfigured operator.
    """
    if signing_key_file is not None and (environ.get(SIGNING_KEY_ENV) or "").strip():
        raise ValueError(
            f"signing key is provided both as ${SIGNING_KEY_ENV} and as --signing-key-file; "
            "provide exactly one source"
        )
    if signing_key_file is not None:
        source = f"file {signing_key_file!r}"
        value = Path(signing_key_file).read_text(encoding="utf-8").strip()
    else:
        source = f"environment variable {SIGNING_KEY_ENV}"
        value = (environ.get(SIGNING_KEY_ENV) or "").strip()
    if not value:
        parser_message = "signing key is required"
        if signing_key_file:
            parser_message = f"signing key file is empty: {signing_key_file}"
        raise ValueError(parser_message)
    try:
        key = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"signing key from {source} must be hex-encoded byte material") from error
    if len(key) != 32:
        raise ValueError(f"signing key from {source} must decode to exactly 32 bytes")
    return key


def _canonical_genesis_payload(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {**_plain_mapping(rule), "scope": SOURCE_HANDLING_RULE_SCOPE}


def _canonical_digest(rule: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(_plain_mapping(rule)).encode("utf-8")).hexdigest()


def _load_production_rule() -> dict[str, Any]:
    """Load the repository-owned production rule, failing closed on any deviation."""
    path = _DEFAULT_RULE
    try:
        rule_raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise SourceHandlingBlockedError(f"production authorization rule is missing: {path}") from error
    except OSError as error:
        raise SourceHandlingBlockedError(f"production authorization rule is unreadable: {path}") from error
    try:
        rule = json.loads(rule_raw)
    except json.JSONDecodeError as error:
        raise SourceHandlingBlockedError(f"production authorization rule is malformed: {path}") from error
    if not isinstance(rule, dict):
        raise SourceHandlingBlockedError(f"production authorization rule must be a JSON object: {path}")
    digest = _canonical_digest(rule)
    if digest != PINNED_PRODUCTION_RULE_SHA256:
        raise SourceHandlingBlockedError(
            f"production authorization rule digest {digest} does not match the pinned canonical "
            f"digest {PINNED_PRODUCTION_RULE_SHA256}; refusing to bootstrap"
        )
    return rule


def _expected_genesis_record_id(rule: Mapping[str, Any]) -> str:
    payload = _canonical_genesis_payload(rule)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _derived_digests(signing_key: bytes, rule: Mapping[str, Any]) -> tuple[str, str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(signing_key)
    verification_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verification_key_hex = verification_key.hex()
    verification_key_sha256 = hashlib.sha256(verification_key).hexdigest()
    genesis_rule_sha256 = _canonical_digest(rule)
    return verification_key_hex, verification_key_sha256, genesis_rule_sha256


def _run(
    database: str,
    signing_key: bytes,
    rule: Mapping[str, Any],
) -> dict[str, Any]:
    verification_key_hex, verification_key_sha256, genesis_rule_sha256 = _derived_digests(signing_key, rule)
    operator_root = SourceHandlingOperatorRoot(
        genesis_rule_sha256=genesis_rule_sha256,
        verification_key_sha256=verification_key_sha256,
    )
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=signing_key,
        operator_root=operator_root,
        provenance_resolver=production_provenance_resolver,
    )
    store = service.resolver()("source-handling-bootstrap", datetime.now(UTC)).store
    existing_head = store.current_canonical_head_id(
        "AUTHORIZATION_RULE",
        SOURCE_HANDLING_RULE_SCOPE,
    )
    expected_genesis_record_id = _expected_genesis_record_id(rule)
    if existing_head is None:
        result = service.publish_genesis_rule(rule)
        genesis_record_id = result.record_id
        status = "bootstrapped"
        root_presence = "pinned"
    else:
        if existing_head != expected_genesis_record_id:
            raise SourceHandlingBlockedError(
                "existing authority genesis does not match the production rule; refusing to re-bootstrap"
            )
        genesis_record_id = existing_head
        status = "already-provisioned"
        root_presence = "verified"
    return {
        "database": database,
        "status": status,
        "operator_root": root_presence,
        "genesis_record_id": genesis_record_id,
        VERIFICATION_KEY_ENV: verification_key_hex,
        VERIFICATION_KEY_SHA256_ENV: verification_key_sha256,
        GENESIS_RULE_SHA256_ENV: genesis_rule_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/bootstrap_source_handling_authority.py",
        description="Provision the production Source Handling authority root and genesis rule.",
    )
    parser.add_argument(
        "--database", required=True, help="explicit evidence database path (e.g. /data/evidence.sqlite)"
    )
    parser.add_argument(
        "--signing-key-file",
        default=None,
        help=f"path to a file containing the hex-encoded Ed25519 signing key (mutually exclusive with ${SIGNING_KEY_ENV})",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable non-secret JSON on success")
    arguments = parser.parse_args(argv)

    try:
        signing_key = _load_signing_key(environ=os.environ, signing_key_file=arguments.signing_key_file)
        rule = _load_production_rule()
        outcome = _run(arguments.database, signing_key, rule)
    except (ValueError, OSError, json.JSONDecodeError, SourceHandlingBlockedError) as error:
        parser.error(str(error))
    if arguments.json:
        print(json.dumps(outcome, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        print(f"[source-handling-bootstrap] {outcome['status']} ({outcome['operator_root']})")
        print(f"{VERIFICATION_KEY_ENV}={outcome[VERIFICATION_KEY_ENV]}")
        print(f"{VERIFICATION_KEY_SHA256_ENV}={outcome[VERIFICATION_KEY_SHA256_ENV]}")
        print(f"{GENESIS_RULE_SHA256_ENV}={outcome[GENESIS_RULE_SHA256_ENV]}")
        print(f"database={outcome['database']}")
        print(f"genesis_record_id={outcome['genesis_record_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
