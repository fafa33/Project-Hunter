from __future__ import annotations

from hashlib import sha256
from typing import Any

from hunter.execution.canonicalization import canonicalize

HASH_FORMAT_VERSION = "hash-v1"


def stable_digest(namespace: str, payload: Any, *, schema_version: str) -> str:
    material = canonicalize(
        {
            "hash_format_version": HASH_FORMAT_VERSION,
            "namespace": namespace,
            "schema_version": schema_version,
            "payload": payload,
        }
    )
    return sha256(material).hexdigest()


def stable_fingerprint(namespace: str, payload: Any, *, schema_version: str = "fingerprint-v1") -> str:
    return f"{namespace}:{schema_version}:{stable_digest(namespace, payload, schema_version=schema_version)}"


def stable_identifier(namespace: str, payload: Any, *, schema_version: str = "identity-v1") -> str:
    return f"{namespace}:{schema_version}:{stable_digest(namespace, payload, schema_version=schema_version)}"
