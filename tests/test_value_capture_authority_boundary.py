from __future__ import annotations

import importlib.util

from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService


def test_caller_constructible_authority_module_does_not_exist() -> None:
    assert importlib.util.find_spec("hunter.value_capture.authority") is None


def test_public_types_expose_no_authoritative_writer_or_mutation_api(tmp_path) -> None:
    repository = SupplyAndValueCaptureRepository(tmp_path / "value-capture.sqlite")
    forbidden = {
        "apply",
        "commit",
        "persist",
        "write",
        "_commit_authoritative",
        "_ValueCaptureAuthorityWriter",
    }
    assert forbidden.isdisjoint(set(dir(repository)))
    assert forbidden.isdisjoint(set(dir(SupplyAndValueCaptureService)))
