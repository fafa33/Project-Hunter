from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import hunter.value_capture.repository as repository_module
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
        "_SupplyAndValueCaptureService__persist_authorized",
        "_SupplyAndValueCaptureService__insert_receipt",
        "_SupplyAndValueCaptureService__insert_record",
        "_SupplyAndValueCaptureService__table_for",
        "_SupplyAndValueCaptureService__validate_receipt_binding",
    }
    assert forbidden.isdisjoint(set(dir(repository)))
    assert forbidden.isdisjoint(set(dir(SupplyAndValueCaptureService)))


def test_raw_write_primitives_are_lexically_scoped_inside_validating_capability() -> None:
    source = inspect.getsource(SupplyAndValueCaptureService)
    assert "def persist_capability(" in source
    assert "self._authorize_result(provider, result, expected_kind=expected_kind)" in source
    assert "self.__verification_keys.verify_receipt(result.receipt)" in source
    assert "def insert_receipt(" in source
    assert "def insert_record(" in source
    assert "self.__persist_capability: PersistCapability = persist_capability" in source


def test_repository_module_exposes_no_connection_or_write_helpers() -> None:
    forbidden = {
        "_connect",
        "_insert_receipt",
        "_insert_record",
        "_table_for",
        "_validate_receipt_binding",
    }
    assert forbidden.isdisjoint(set(dir(repository_module)))


def test_temporary_value_capture_autofix_workflows_are_absent() -> None:
    assert not Path(".github/workflows/value-capture-black-fix.yml").exists()
    assert not Path(".github/workflows/value-capture-seal-write-fix.yml").exists()
    assert not Path(".github/workflows/value-capture-capability-fix.yml").exists()
