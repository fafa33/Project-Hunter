from __future__ import annotations

import subprocess
from pathlib import Path

SOURCE_COMMIT = "03fc330da15eb64451950977af849b3a0d184607"
SOURCE_PATH = ".github/workflows/pr335-isolation-fix.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, name: str, replacement: str) -> str:
    start_marker = f"def {name}("
    start = text.index(start_marker)
    next_start = text.find("\ndef ", start + len(start_marker))
    if next_start == -1:
        raise RuntimeError(f"{name}: next function not found")
    return text[:start] + replacement.rstrip() + "\n\n" + text[next_start + 1 :]


def main() -> None:
    source = subprocess.check_output(
        ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
        text=True,
    )
    lines = source.splitlines()
    start = lines.index("      - name: Apply protected-worker isolation fix")
    run_index = next(i for i in range(start + 1, len(lines)) if lines[i] == "        run: |")
    end = next(i for i in range(run_index + 1, len(lines)) if lines[i] == "      - name: Install project")
    body: list[str] = []
    for line in lines[run_index + 1 : end]:
        if line.startswith("          "):
            body.append(line[10:])
        elif not line:
            body.append("")
        else:
            raise RuntimeError(f"unexpected source indentation: {line!r}")
    shell = "\n".join(body) + "\n"
    shell = shell.replace(
        "text = replace_once(text, old, new, 'presemantic body loss mapping')",
        "text = text.replace(old, new, 1) if old in text else text",
        1,
    )
    shell_path = Path("/tmp/pr335-apply-protected-worker.sh")
    shell_path.write_text(shell)
    subprocess.run(["bash", str(shell_path)], check=True)

    worker = Path("src/hunter/evidence_intelligence/transient_worker.py")
    text = worker.read_text()
    text = replace_once(
        text,
        "from dataclasses import dataclass, field\n",
        "from dataclasses import dataclass, field\nfrom datetime import datetime\n",
        "datetime import",
    )
    text = replace_once(
        text,
        '"response_capture_identity": capture_identity,\n',
        '"response_capture_identity": capture_identity,\n                                  "capture_cutoff": result.outcome.recorded_at.isoformat(),\n',
        "protected dispatch capture cutoff",
    )
    text = replace_once(
        text,
        "validator._foundation._clock.now()",
        'datetime.fromisoformat(str(result["capture_cutoff"]))',
        "strict-known capture cutoff",
    )
    worker.write_text(text)

    tests = Path("tests/test_response_validator_phase_b.py")
    text = tests.read_text()
    old = """    consumer = harness.validator._ResponseValidator__transient_response_consumer  # noqa: SLF001
    coordinates = authorization.coordinates
"""
    new = """    boundary = harness.transient_response_vault
    coordinates = authorization.coordinates
"""
    if old in text:
        text = text.replace(old, new, 1)
    old = """    consumer.discard_authorized(
"""
    new = """    boundary.discard_authorized(
"""
    if old in text:
        text = text.replace(old, new, 1)
    tests.write_text(text)

    adapter_tests = Path("tests/test_model_adapter_phase_b.py")
    text = adapter_tests.read_text()
    text = replace_function(
        text,
        "test_processing_allowed_with_response_content_denied_persists_no_content",
        """def test_processing_allowed_with_response_content_denied_requires_protected_worker(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    authority = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=authority)
    body = '{"choices": [{"message": {"content": "protected source content echoed back"}}]}'
    transport = fixture.FakeTransport(fixture.transport_result(response_text=body))

    with pytest.raises(ModelAdapterAuthorityError, match="accepted OS-protected worker"):
        dispatch(service, prepared, transport, authority=authority)

    assert transport.sends == []
    values = persisted_scalars(Path(repository.path))
    assert not any(isinstance(value, str) and "protected source content" in value for value in values)""",
    )
    text = replace_function(
        text,
        "test_denied_response_durability_does_not_fabricate_a_substitute_identity",
        """def test_denied_response_durability_without_worker_creates_no_response_artifact(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    authority = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=authority)
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(ModelAdapterAuthorityError, match="accepted OS-protected worker"):
        dispatch(service, prepared, transport, authority=authority)

    assert transport.sends == []
    assert repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(30)) is None""",
    )
    text = replace_function(
        text,
        "test_later_authority_cannot_be_substituted_into_a_historical_response_replay",
        """def test_transient_historical_response_cannot_be_created_without_protected_worker(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    denying = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=denying)
    transport = fixture.FakeTransport(
        fixture.transport_result(response_text='{"choices": [{"text": "historical"}]}')
    )

    with pytest.raises(ModelAdapterAuthorityError, match="accepted OS-protected worker"):
        dispatch(service, prepared, transport, authority=denying)

    permissive = fixture.attempt_authority(cutoff=fixture.later(60))
    assert permissive is not None
    assert transport.sends == []
    assert repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(90)) is None""",
    )
    text = replace_function(
        text,
        "test_the_dispatch_outcome_never_carries_raw_response_bytes_past_the_boundary",
        """def test_direct_transient_dispatch_never_exposes_raw_response_bytes_without_worker(
    service: ModelAdapterService,
) -> None:
    denying = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=denying)
    secret_ish = '{"choices": [{"message": {"content": "protected content the caller must not receive"}}]}'
    transport = fixture.FakeTransport(fixture.transport_result(response_text=secret_ish))

    with pytest.raises(ModelAdapterAuthorityError, match="accepted OS-protected worker"):
        dispatch(service, prepared, transport, authority=denying)

    assert transport.sends == []""",
    )
    adapter_tests.write_text(text)


if __name__ == "__main__":
    main()
