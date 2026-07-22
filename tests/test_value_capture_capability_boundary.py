from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from test_value_capture_v3_5_0 import evidence_result, setup


def test_persistence_capability_derives_record_and_rejects_caller_supplied_record(tmp_path) -> None:
    service, repository, provider = setup(tmp_path)
    result = evidence_result(provider)
    canonical = service._record_from_result(result, expected_kind="evidence")
    divergent = replace(canonical, extracted_claim="caller-supplied divergent claim")
    capability = getattr(service, "_SupplyAndValueCaptureService__persist_capability")

    assert tuple(inspect.signature(capability).parameters) == (
        "provider",
        "result",
        "expected_kind",
    )

    with pytest.raises(TypeError):
        capability(provider, result, divergent, "evidence")

    assert repository.count("value_capture_acquisition_receipts") == 0
    assert repository.count("fundamental_evidence_records") == 0
