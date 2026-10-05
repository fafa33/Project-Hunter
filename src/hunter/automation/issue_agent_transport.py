"""Sealed, identity-bound transport between isolated Issue Agent runners (ADR 0037 D3, owner decision B-1).

Payloads crossing a job boundary are sealed so that a public Actions artifact never carries a plaintext
prompt, hostile result, INTERNAL evidence or secret. Those payloads are the compiled handoff (control →
executor) and the hostile result (executor → validator/publisher).

The envelope is X25519 key agreement → HKDF-SHA256 → ChaCha20-Poly1305. Its associated data binds the
payload to:

- schema version and payload kind;
- repository and Issue;
- authorization, exact base and canonical TaskScope;
- execution identity and handoff digest;
- plaintext digest and recipient key.

A receiver refuses **before decrypting** unless the associated data equals the expectation it derived from
the signed ledger. After decrypting it re-checks the plaintext digest.

Transport is never state, authority or a database. Every failure is ``TransportIntegrityError``, which the
lifecycle records as ``TRANSPORT_INTEGRITY_FAILED``. A missing, expired or mismatched transport never
reruns the model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

TRANSPORT_SCHEMA_VERSION: Final = "hunter-ia-sealed-transport-v1"
PAYLOAD_KINDS: Final = frozenset({"handoff", "result"})
MAX_PLAINTEXT_BYTES: Final = 8 * 1024 * 1024
_ENVELOPE_FIELDS: Final = frozenset({"aad", "epk", "nonce", "ciphertext"})
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX24 = re.compile(r"[0-9a-f]{24}")
_AUTHORIZATION_ID = re.compile(r"hunter-issue-agent-authorization:[0-9a-f]{64}")


class TransportIntegrityError(RuntimeError):
    """The sealed transport is missing, malformed, mismatched or unauthentic. Fail closed."""

    failure_code = "TRANSPORT_INTEGRITY_FAILED"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def recipient_key_id(public_key: X25519PublicKey) -> str:
    return hashlib.sha256(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()


@dataclass(frozen=True, slots=True)
class TransportBinding:
    """The authenticated associated data. Every field is derived by the receiver from the signed ledger."""

    payload_kind: str
    repository_id: int
    issue_number: int
    authorization_id: str
    base_sha: str
    task_scope_sha256: str
    execution_id: str
    handoff_sha256: str
    plaintext_sha256: str
    recipient_key_id: str
    schema_version: str = TRANSPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRANSPORT_SCHEMA_VERSION or self.payload_kind not in PAYLOAD_KINDS:
            raise TransportIntegrityError("transport schema or payload kind is outside the closed vocabulary")
        for name in ("repository_id", "issue_number"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise TransportIntegrityError(f"{name} must be a positive integer")
        if _SHA40.fullmatch(self.base_sha) is None:
            raise TransportIntegrityError("base_sha must be an exact commit SHA")
        if not isinstance(self.authorization_id, str) or _AUTHORIZATION_ID.fullmatch(self.authorization_id) is None:
            raise TransportIntegrityError("authorization_id must be a canonical authorization identity")
        for name in (
            "task_scope_sha256",
            "execution_id",
            "handoff_sha256",
            "plaintext_sha256",
            "recipient_key_id",
        ):
            if not isinstance(getattr(self, name), str) or _SHA64.fullmatch(getattr(self, name)) is None:
                raise TransportIntegrityError(f"{name} must be a SHA-256 hex digest")
        if self.payload_kind == "handoff" and self.handoff_sha256 != self.plaintext_sha256:
            raise TransportIntegrityError("a handoff envelope's plaintext is the handoff itself")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.as_dict())).hexdigest()


def _key(shared: bytes, ephemeral_public: bytes, payload_kind: str) -> bytes:
    info = f"{TRANSPORT_SCHEMA_VERSION}:{payload_kind}".encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral_public, info=info).derive(shared)


def seal(plaintext: bytes, *, recipient: X25519PublicKey, binding: TransportBinding) -> bytes:
    """Seal ``plaintext`` to ``recipient`` under ``binding``. Returns the canonical envelope bytes."""

    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise TransportIntegrityError("payload exceeds the transport bound")
    if binding.recipient_key_id != recipient_key_id(recipient):
        raise TransportIntegrityError("binding names a different recipient key")
    if binding.plaintext_sha256 != hashlib.sha256(plaintext).hexdigest():
        raise TransportIntegrityError("binding does not name this plaintext")
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = _key(ephemeral.exchange(recipient), ephemeral_public, binding.payload_kind)
    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, _canonical(binding.as_dict()))
    return _canonical(
        {
            "aad": binding.as_dict(),
            "epk": ephemeral_public.hex(),
            "nonce": nonce.hex(),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
    )


def _parse(envelope: bytes) -> dict[str, Any]:
    if not isinstance(envelope, bytes | bytearray) or len(envelope) > MAX_PLAINTEXT_BYTES * 2:
        raise TransportIntegrityError("envelope is missing or exceeds the transport bound")
    try:
        document = json.loads(envelope)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TransportIntegrityError("envelope is not canonical JSON") from None
    if not isinstance(document, dict) or set(document) != _ENVELOPE_FIELDS or not isinstance(document["aad"], dict):
        raise TransportIntegrityError("envelope fields differ from the closed schema")
    if _HEX64.fullmatch(str(document["epk"])) is None or _HEX24.fullmatch(str(document["nonce"])) is None:
        raise TransportIntegrityError("envelope key material is malformed")
    return document


def header(envelope: bytes) -> TransportBinding:
    """Read the (unauthenticated until opened) binding. Used to refuse a mismatched artifact without a key."""

    document = _parse(envelope)
    try:
        return TransportBinding(**document["aad"])
    except TypeError:
        raise TransportIntegrityError("envelope binding fields differ from the closed schema") from None


def open_sealed(envelope: bytes, *, recipient: X25519PrivateKey, expected: TransportBinding) -> bytes:
    """Open ``envelope`` only if it is bound exactly to ``expected``; return the verified plaintext."""

    if header(envelope) != expected:
        raise TransportIntegrityError("envelope binding does not match the signed ledger")
    document = _parse(envelope)
    try:
        ciphertext = base64.b64decode(document["ciphertext"], validate=True)
        ephemeral_public = bytes.fromhex(document["epk"])
        shared = recipient.exchange(X25519PublicKey.from_public_bytes(ephemeral_public))
        key = _key(shared, ephemeral_public, expected.payload_kind)
        plaintext = ChaCha20Poly1305(key).decrypt(
            bytes.fromhex(document["nonce"]), ciphertext, _canonical(document["aad"])
        )
    except (InvalidTag, ValueError, TypeError):
        raise TransportIntegrityError("envelope failed authentication") from None
    if hashlib.sha256(plaintext).hexdigest() != expected.plaintext_sha256:
        raise TransportIntegrityError("plaintext digest does not match the binding")
    return plaintext
