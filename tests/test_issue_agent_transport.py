"""ADR 0037 D3 / owner decision B-1: sealed, identity-bound transport between isolated runners."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hunter.automation import issue_agent_transport as transport
from hunter.automation.issue_agent_transport import TransportBinding, TransportIntegrityError

RECIPIENT = X25519PrivateKey.generate()
PAYLOAD = b'{"files":[{"path":"docs/ISSUE_AGENT_CANARY.md","content":"canary"}]}'


def binding(payload: bytes = PAYLOAD, kind: str = "result", **overrides: object) -> TransportBinding:
    digest = hashlib.sha256(payload).hexdigest()
    fields: dict[str, object] = {
        "payload_kind": kind,
        "repository_id": 1,
        "issue_number": 520,
        "authorization_id": "hunter-issue-agent-authorization:" + "a" * 64,
        "base_sha": "b" * 40,
        "task_scope_sha256": "c" * 64,
        "execution_id": "e" * 64,
        "handoff_sha256": digest if kind == "handoff" else "d" * 64,
        "plaintext_sha256": digest,
        "recipient_key_id": transport.recipient_key_id(RECIPIENT.public_key()),
    }
    fields.update(overrides)
    return TransportBinding(**fields)  # type: ignore[arg-type]


def sealed(payload: bytes = PAYLOAD, kind: str = "result") -> bytes:
    return transport.seal(payload, recipient=RECIPIENT.public_key(), binding=binding(payload, kind))


def test_a_bound_envelope_opens_to_the_exact_plaintext() -> None:
    assert transport.open_sealed(sealed(), recipient=RECIPIENT, expected=binding()) == PAYLOAD
    handoff = b"compiled handoff bytes"
    assert (
        transport.open_sealed(sealed(handoff, "handoff"), recipient=RECIPIENT, expected=binding(handoff, "handoff"))
        == handoff
    )


def test_the_public_envelope_never_contains_the_plaintext() -> None:
    envelope = sealed()
    assert PAYLOAD not in envelope and b"ISSUE_AGENT_CANARY" not in envelope


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authorization_id", "hunter-issue-agent-authorization:" + "0" * 64),
        ("issue_number", 521),
        ("repository_id", 2),
        ("base_sha", "0" * 40),
        ("task_scope_sha256", "0" * 64),
        ("execution_id", "0" * 64),
        ("handoff_sha256", "0" * 64),
    ],
)
def test_an_envelope_bound_to_any_other_identity_is_refused_before_decryption(field: str, value: object) -> None:
    with pytest.raises(TransportIntegrityError, match="does not match"):
        transport.open_sealed(sealed(), recipient=RECIPIENT, expected=dataclasses.replace(binding(), **{field: value}))


def test_a_handoff_cannot_be_opened_as_a_result_or_vice_versa() -> None:
    handoff = b"handoff"
    with pytest.raises(TransportIntegrityError):
        transport.open_sealed(sealed(handoff, "handoff"), recipient=RECIPIENT, expected=binding(handoff, "result"))


def test_rewriting_the_associated_data_breaks_authentication() -> None:
    document = json.loads(sealed())
    document["aad"]["authorization_id"] = "hunter-issue-agent-authorization:" + "0" * 64
    forged = json.dumps(document).encode()
    with pytest.raises(TransportIntegrityError, match="authentication"):
        transport.open_sealed(
            forged,
            recipient=RECIPIENT,
            expected=binding(authorization_id="hunter-issue-agent-authorization:" + "0" * 64),
        )


def test_corrupted_ciphertext_is_refused() -> None:
    document = json.loads(sealed())
    raw = bytearray(base64.b64decode(document["ciphertext"]))
    raw[0] ^= 1
    document["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(TransportIntegrityError, match="authentication"):
        transport.open_sealed(json.dumps(document).encode(), recipient=RECIPIENT, expected=binding())


def test_a_different_recipient_cannot_open_the_envelope() -> None:
    other = X25519PrivateKey.generate()
    expected = binding(recipient_key_id=transport.recipient_key_id(other.public_key()))
    with pytest.raises(TransportIntegrityError):
        transport.open_sealed(sealed(), recipient=other, expected=expected)


def test_a_sender_cannot_misdeclare_the_plaintext_digest() -> None:
    with pytest.raises(TransportIntegrityError, match="does not name this plaintext"):
        transport.seal(PAYLOAD, recipient=RECIPIENT.public_key(), binding=binding(b"something else"))


def _hostile_seal(plaintext: bytes, aad: TransportBinding) -> bytes:
    """What a compromised executor can do: run the envelope construction without any honesty check."""

    ephemeral = X25519PrivateKey.generate()
    epk = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = transport._key(ephemeral.exchange(RECIPIENT.public_key()), epk, aad.payload_kind)
    nonce = b"\x00" * 12
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, transport._canonical(aad.as_dict()))
    return json.dumps(
        {
            "aad": aad.as_dict(),
            "epk": epk.hex(),
            "nonce": nonce.hex(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
    ).encode()


def test_a_hostile_sender_lying_about_the_digest_is_caught_after_authentication() -> None:
    lie = binding(plaintext_sha256="0" * 64)
    with pytest.raises(TransportIntegrityError, match="plaintext digest"):
        transport.open_sealed(_hostile_seal(PAYLOAD, lie), recipient=RECIPIENT, expected=lie)


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json", id="not-json"),
        pytest.param(json.dumps({"aad": {}, "epk": "x", "nonce": "y"}).encode(), id="missing-field"),
        pytest.param(
            json.dumps({"aad": {}, "epk": "0" * 64, "nonce": "0" * 24, "ciphertext": "", "extra": 1}).encode(),
            id="extra-field",
        ),
    ],
)
def test_malformed_envelopes_are_refused(envelope: bytes) -> None:
    with pytest.raises(TransportIntegrityError):
        transport.open_sealed(envelope, recipient=RECIPIENT, expected=binding())


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"payload_kind": "prompt"}, id="unknown-kind"),
        pytest.param({"issue_number": True}, id="bool-issue"),
        pytest.param({"base_sha": "main"}, id="symbolic-base"),
        pytest.param({"schema_version": "hunter-ia-sealed-transport-v0"}, id="old-schema"),
    ],
)
def test_bindings_outside_the_closed_vocabulary_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(TransportIntegrityError):
        binding(**overrides)


def test_a_valid_envelope_with_a_smuggled_field_is_refused() -> None:
    document = json.loads(sealed())
    document["note"] = "plaintext smuggled beside the ciphertext"
    with pytest.raises(TransportIntegrityError, match="closed schema"):
        transport.open_sealed(json.dumps(document).encode(), recipient=RECIPIENT, expected=binding())


def test_a_sender_cannot_seal_to_a_key_other_than_the_bound_recipient() -> None:
    other = X25519PrivateKey.generate()
    with pytest.raises(TransportIntegrityError, match="different recipient"):
        transport.seal(PAYLOAD, recipient=other.public_key(), binding=binding())


def test_oversized_payloads_are_refused() -> None:
    big = b"x" * (transport.MAX_PLAINTEXT_BYTES + 1)
    with pytest.raises(TransportIntegrityError, match="bound"):
        transport.seal(big, recipient=RECIPIENT.public_key(), binding=binding(big))


def test_the_header_is_readable_without_a_key_for_binding_checks() -> None:
    assert transport.header(sealed()) == binding()
