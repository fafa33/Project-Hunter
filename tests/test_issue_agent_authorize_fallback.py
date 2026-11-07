"""ADR 0041 F3: authorize seals the identical handoff a second time, to X_FB, under the attempt-2 identity."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
import test_issue_agent_authorize as base
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from test_issue_agent_authorize import (
    RECIPIENT,
    TRUST,
    bootstrap_ledger,
    context,
    dependencies,
    document,
    store,
    trusted,
    uploaded,
)

from hunter.automation import issue_agent_authorize as authorize
from hunter.automation import issue_agent_state as state
from hunter.automation import issue_agent_transport as transport

world = base.world
_prompt_keys = base._prompt_keys
FALLBACK = X25519PrivateKey.generate()


def prepare_with_fallback(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[Any, ...]:
    sink: list[bytes] = []
    deps = dependencies(world, monkeypatch, fallback_handoff_recipient=FALLBACK.public_key())
    prepared, sealed = authorize.prepare(
        document(),
        dependencies=deps,
        context=context(),
        state_store=store(world, f"{name}-state"),
        source_handling_store=store(world, f"{name}-sh"),
        workdir=world["tmp"] / f"work-{name}",
        fallback_sink=sink.append,
    )
    assert len(sink) == 1
    return prepared, sealed, sink[0], deps


def fallback_upload(prepared: authorize.Prepared, **overrides: Any) -> authorize.UploadedArtifact:
    fields: dict[str, Any] = dict(
        run_id=100,
        artifact_id=56,
        name=state.fallback_handoff_artifact_name(prepared.authorization_id),
        artifact_digest="sha256:" + "f" * 64,
    )
    fields.update(overrides)
    return authorize.UploadedArtifact(**fields)


def test_both_seals_carry_the_identical_bundle_under_distinct_keys_and_identities(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    prepared, sealed, second, deps = prepare_with_fallback(world, monkeypatch, "dual")
    primary_binding = transport.TransportBinding(**prepared.handoff_binding)
    fallback_binding = transport.TransportBinding(**prepared.fallback_handoff_binding)
    assert transport.open_sealed(sealed, recipient=RECIPIENT, expected=primary_binding) == transport.open_sealed(
        second, recipient=FALLBACK, expected=fallback_binding
    )
    assert fallback_binding.execution_id != primary_binding.execution_id
    assert fallback_binding.recipient_key_id != primary_binding.recipient_key_id
    # Neither key opens the other attempt's seal, and neither seal opens under the other attempt's binding.
    with pytest.raises(transport.TransportIntegrityError):
        transport.open_sealed(second, recipient=RECIPIENT, expected=fallback_binding)
    with pytest.raises(transport.TransportIntegrityError):
        transport.open_sealed(second, recipient=FALLBACK, expected=primary_binding)
    assert b"ISSUE_AGENT_CANARY" not in second

    authorize.commit(
        prepared,
        uploaded=uploaded(prepared),
        fallback_uploaded=fallback_upload(prepared),
        dependencies=deps,
        context=context(),
        state_store=store(world, "dual-commit"),
        source_handling_store=store(world, "dual-commit-sh"),
    )
    _, entries = store(world, "dual-verify").read(520)
    view = state.verify_chain(
        [e.record for e in entries],
        repository_id=1,
        issue_number=520,
        trust=TRUST,
        provenance=trusted,
        indexes=[e.index for e in entries],
    )
    offer = view.authorizations[prepared.authorization_id].evidence[state.AUTHORIZED]["fallback"]
    assert offer["execution_id"] == fallback_binding.execution_id
    assert offer["handoff_artifact"]["ciphertext_sha256"] == state.sha256_hex(second)
    assert offer["handoff_artifact"]["aad_sha256"] == fallback_binding.digest()


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "hunter-ia-handoff-" + "0" * 64},  # the primary's name, or anything else
        {"run_id": 101},
        {"artifact_id": 55},  # the very artifact bound as the primary handoff
        {"expired": True},
        {"artifact_digest": "md5:abc"},
    ],
)
def test_a_fallback_upload_that_does_not_bind_this_run_refuses(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    bootstrap_ledger(world)
    prepared, _sealed, _second, deps = prepare_with_fallback(world, monkeypatch, "bad")
    with pytest.raises(authorize.AuthorizeRefused) as refused:
        authorize.commit(
            prepared,
            uploaded=uploaded(prepared),
            fallback_uploaded=fallback_upload(prepared, **overrides),
            dependencies=deps,
            context=context(),
            state_store=store(world, "bad-commit"),
            source_handling_store=store(world, "bad-commit-sh"),
        )
    assert refused.value.code == "TRANSPORT_INTEGRITY_FAILED"


def test_a_prepared_offer_without_its_upload_or_an_upload_without_an_offer_refuses(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    prepared, _sealed, _second, deps = prepare_with_fallback(world, monkeypatch, "missing")
    with pytest.raises(authorize.AuthorizeRefused):
        authorize.commit(
            prepared,
            uploaded=uploaded(prepared),
            dependencies=deps,
            context=context(),
            state_store=store(world, "missing-commit"),
            source_handling_store=store(world, "missing-commit-sh"),
        )
    plain = json.loads(prepared.to_json())
    plain.update(fallback_handoff_binding=None, fallback_handoff_ciphertext_sha256=None)
    with pytest.raises(authorize.AuthorizeRefused):
        authorize.commit(
            authorize.Prepared(**plain),
            uploaded=uploaded(prepared),
            fallback_uploaded=fallback_upload(prepared),
            dependencies=deps,
            context=context(),
            state_store=store(world, "extra-commit"),
            source_handling_store=store(world, "extra-commit-sh"),
        )


@pytest.mark.parametrize("case", ["recipient-without-sink", "sink-without-recipient", "same-recipient"])
def test_a_misconfigured_fallback_refuses_before_anything_is_sealed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    bootstrap_ledger(world)
    recipient = {
        "recipient-without-sink": FALLBACK.public_key(),
        "sink-without-recipient": None,
        "same-recipient": RECIPIENT.public_key(),
    }[case]
    sink: list[bytes] = []
    with pytest.raises(authorize.AuthorizeRefused):
        authorize.prepare(
            document(),
            dependencies=dependencies(world, monkeypatch, fallback_handoff_recipient=recipient),
            context=context(),
            state_store=store(world, f"{case}-state"),
            source_handling_store=store(world, f"{case}-sh"),
            workdir=world["tmp"] / f"work-{case}",
            fallback_sink=None if case == "recipient-without-sink" else sink.append,
        )
    assert sink == []


def test_without_a_fallback_recipient_the_prepared_record_is_unchanged(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    (prepared, _sealed), _deps = base.run_prepare(world, monkeypatch, "plain")
    assert prepared.fallback_handoff_binding is None and prepared.fallback_handoff_ciphertext_sha256 is None
    assert "fallback" not in prepared.unsigned_authorized


# --- ADR 0041 F8: the owner-armed canary -----------------------------------------------------------------------


def _label_events(armed: bool) -> list[dict[str, Any]]:
    authorized = state._instant(base.UPDATED_AT)
    before = (authorized - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    canary = [
        {
            "event": "labeled",
            "label": {"name": authorize.FALLBACK_CANARY_LABEL},
            "actor": {"login": base.OWNER},
            "created_at": before,
        }
    ]
    execute = {
        "event": "labeled",
        "label": {"name": base.ISSUE_AGENT_AUTHORIZATION_LABEL},
        "actor": {"login": base.OWNER},
        "created_at": base.UPDATED_AT,
    }
    return [*(canary if armed else []), execute]


@pytest.mark.parametrize("armed", [True, False])
def test_the_signed_authorization_records_whether_the_owner_armed_the_canary(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, armed: bool
) -> None:
    bootstrap_ledger(world)
    sink: list[bytes] = []
    deps = dependencies(
        world,
        monkeypatch,
        fallback_handoff_recipient=FALLBACK.public_key(),
        issue_label_events=lambda issue: _label_events(armed),
    )
    name = f"canary-{armed}"
    prepared, _ = authorize.prepare(
        document(),
        dependencies=deps,
        context=context(),
        state_store=store(world, f"{name}-state"),
        source_handling_store=store(world, f"{name}-sh"),
        workdir=world["tmp"] / f"work-{name}",
        fallback_sink=sink.append,
    )
    assert prepared.fallback_canary is armed
    assert authorize.Prepared.from_json(prepared.to_json()).fallback_canary is armed
    authorize.commit(
        prepared,
        uploaded=uploaded(prepared),
        fallback_uploaded=fallback_upload(prepared),
        dependencies=deps,
        context=context(),
        state_store=store(world, f"{name}-commit"),
        source_handling_store=store(world, f"{name}-commit-sh"),
    )
    _, entries = store(world, f"{name}-verify").read(520)
    view = state.verify_chain(
        [e.record for e in entries],
        repository_id=1,
        issue_number=520,
        trust=TRUST,
        provenance=trusted,
        indexes=[e.index for e in entries],
    )
    assert view.authorizations[prepared.authorization_id].evidence[state.AUTHORIZED]["fallback_canary"] is armed


def test_an_indefinite_label_read_refuses_rather_than_disarming(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)

    def unavailable(issue: int) -> list[dict[str, Any]]:
        raise ConnectionError("indefinite")

    deps = dependencies(
        world, monkeypatch, fallback_handoff_recipient=FALLBACK.public_key(), issue_label_events=unavailable
    )
    with pytest.raises(authorize.AuthorizeRefused) as refused:
        authorize.prepare(
            document(),
            dependencies=deps,
            context=context(),
            state_store=store(world, "indefinite-state"),
            source_handling_store=store(world, "indefinite-sh"),
            workdir=world["tmp"] / "work-indefinite",
            fallback_sink=[].append,
        )
    assert refused.value.code == "NOT_ELIGIBLE"
