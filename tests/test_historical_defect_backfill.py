from __future__ import annotations

import hashlib
import json

import hunter_defect_prevention_preflight as prevention
import hunter_pre_ready_review as review


def _family(family_id: str = "DFF-023", *, regression: list[str] | None = None) -> dict[str, object]:
    return {
        "id": family_id,
        "title": "preflight-not-enforced-before-ledger-claim-on-every-execution-entry-point",
        "invariant": "A governed acceptance may not claim the execution ledger before Source Handling validation succeeds, on every execution entry point.",
        "lifecycle": "regression-tested",
        "applicability": {
            "changed_paths": ["src/hunter/automation/", "scripts/"],
            "rationale": "The execution ledger and the deployed issuer edge are the entry points that can claim an acceptance before validation.",
        },
        "prevention": {
            "mechanism": "Both entry points resolve Source Handling authority in a shared preparation seam before any ledger claim.",
            "boundary": "review",
            "guard_reference": "scripts/hunter_issue_agent_issuer.py::prepare_authorization",
        },
        "regression_evidence": (
            regression
            if regression is not None
            else [
                "tests/test_issue_agent_execution.py::test_preflight_failure_creates_no_ledger_row_and_allows_retry",
                "tests/test_issue_agent_issuer.py::test_missing_source_handling_authority_fails_closed_422",
            ]
        ),
        "sources": ["Issue #449 (2026-09-10)"],
    }


def _minimal_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "HBF-999-001",
        "source_pr": 999,
        "source_reference": "reviewer inline comment 9999999999",
        "reviewer": "chatgpt-codex-connector[bot]",
        "severity": "P1",
        "classification": "confirmed",
        "original_defect": "The deployed issuer entry point claims the ledger before the Source Handling preflight runs.",
        "canonical_family": "DFF-023",
        "dff_id": "DFF-023",
        "fix_reference": "PR #999",
        "regression_test_reference": "tests/test_issue_agent_execution.py::test_preflight_failure_creates_no_ledger_row_and_allows_retry",
        "selector_reference": "DFF-023 applicability changed_paths declares the execution seams",
        "gate_reference": "scripts/hunter_issue_agent_issuer.py::prepare_authorization",
        "status": "guarded",
    }
    record.update(overrides)
    return record


def _write_backfill(tmp_path, records: list[dict[str, object]], *, pull_requests: list[int] | None = None) -> object:
    backfill = {
        "version": 1,
        "purpose": "test",
        "window": {"pull_requests": pull_requests or [999]},
        "records": records,
        "manifest": prevention.historical_manifest(records),
    }
    path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    path.write_text(json.dumps(backfill, indent=2), encoding="utf-8")
    return path


def _patch_backfill(monkeypatch, tmp_path, records: list[dict[str, object]]) -> None:
    path = _write_backfill(tmp_path, records)
    monkeypatch.setattr(prevention, "BACKFILL_PATH", path)
    manifest = prevention.historical_manifest(records)
    monkeypatch.setattr(
        prevention,
        "TRUSTED_BACKFILL_MANIFEST_DIGEST",
        hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )


def _patch_registry(monkeypatch, tmp_path, families: list[dict[str, object]]) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "defects": [], "families": families}, indent=2), encoding="utf-8")
    monkeypatch.setattr(prevention, "REGISTRY_PATH", path)


# --------------------------------------------------------------------------
# Canonical artifact: the historical window's classification is valid and the
# zero-recurrence targets hold on the curated record set.
# --------------------------------------------------------------------------


def test_the_canonical_historical_defect_backfill_is_valid() -> None:
    assert prevention.validate_historical_defect_backfill() == []


def test_the_canonical_historical_backfill_reports_actual_protection_coverage() -> None:
    coverage = prevention.historical_backfill_coverage()
    assert coverage["confirmed_records"] > 0
    assert (
        coverage["excluded_records"] > 0
    ), "false-positive/obsolete/style findings are recorded but excluded from coverage"
    assert coverage["needs_guard_records"] == 0, "every confirmed real defect must be guarded, none left needs-guard"
    assert coverage["guarded_records"] == coverage["confirmed_records"]
    assert coverage["duplicates_unmapped"] == 0
    for key in ("families_without_regression", "families_without_selector"):
        assert coverage[key] == 0
    # Four protection layers hold for every confirmed historical family.
    assert coverage["confirmed_families"] > 0
    assert coverage["families_with_regression"] == coverage["confirmed_families"]
    assert coverage["families_with_selector"] == coverage["confirmed_families"]
    assert coverage["families_without_gate"] > 0
    assert coverage["families_with_gate"] + coverage["families_without_gate"] == coverage["confirmed_families"]


# --------------------------------------------------------------------------
# Record-level schema and protection requirements fail closed.
# --------------------------------------------------------------------------


def test_backfill_rejects_an_unknown_classification(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record(classification="maybe")])
    errors = prevention.validate_historical_defect_backfill()
    assert any("unknown classification" in error for error in errors)


def test_backfill_rejects_an_unknown_status(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record(status="mystery")])
    errors = prevention.validate_historical_defect_backfill()
    assert any("unknown status" in error for error in errors)


def test_backfill_rejects_an_unknown_severity(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record(severity="P9")])
    errors = prevention.validate_historical_defect_backfill()
    assert any("unknown severity" in error for error in errors)


def test_backfill_rejects_a_confirmed_record_without_its_family(monkeypatch, tmp_path) -> None:
    record = _minimal_record(dff_id="DFF-404", canonical_family="DFF-404")
    _patch_backfill(monkeypatch, tmp_path, [record])
    errors = prevention.validate_historical_defect_backfill()
    assert any("DFF-404" in error for error in errors)


def test_backfill_rejects_a_guarded_record_without_a_resolvable_regression_test(monkeypatch, tmp_path) -> None:
    record = _minimal_record(regression_test_reference="tests/test_does_not_exist.py::test_nothing")
    _patch_backfill(monkeypatch, tmp_path, [record])
    errors = prevention.validate_historical_defect_backfill()
    assert any("regression_test_reference" in error for error in errors)


def test_backfill_rejects_a_guarded_record_without_a_resolvable_gate(monkeypatch, tmp_path) -> None:
    record = _minimal_record(gate_reference="scripts/does_not_exist.py::nope")
    _patch_backfill(monkeypatch, tmp_path, [record])
    errors = prevention.validate_historical_defect_backfill()
    assert any("gate_reference" in error for error in errors)


def test_backfill_rejects_a_needs_guard_record(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record(status="needs-guard")])
    errors = prevention.validate_historical_defect_backfill()
    assert any("is not guarded" in error for error in errors)


def test_backfill_rejects_an_excluded_record_that_points_at_a_family(monkeypatch, tmp_path) -> None:
    record = _minimal_record(classification="style-non-defect", status="obsolete", dff_id="DFF-023")
    _patch_backfill(monkeypatch, tmp_path, [record])
    errors = prevention.validate_historical_defect_backfill()
    assert any("must carry dff_id 'none'" in error for error in errors)


def test_backfill_rejects_a_source_pr_outside_the_declared_window(monkeypatch, tmp_path) -> None:
    record = _minimal_record(source_pr=1234)
    _patch_backfill(monkeypatch, tmp_path, [record])
    errors = prevention.validate_historical_defect_backfill()
    assert any("not declared in the window" in error for error in errors)


def test_backfill_rejects_duplicate_record_ids(monkeypatch, tmp_path) -> None:
    _patch_backfill(
        monkeypatch,
        tmp_path,
        [_minimal_record(), _minimal_record(id="HBF-999-001", dff_id="DFF-404", canonical_family="DFF-404")],
    )
    errors = prevention.validate_historical_defect_backfill()
    assert any("duplicate record id" in error for error in errors)


def test_backfill_rejects_a_duplicate_mapping_away_from_a_confirmed_family(monkeypatch, tmp_path) -> None:
    confirmed = _minimal_record()
    duplicate = _minimal_record(
        id="HBF-999-002",
        classification="duplicate",
        status="duplicate",
        severity="none",
        original_defect="repeated finding for the same defect",
        dff_id="DFF-404",
        canonical_family="DFF-404",
    )
    _patch_backfill(monkeypatch, tmp_path, [confirmed, duplicate])
    errors = prevention.validate_historical_defect_backfill()
    assert any("duplicate maps" in error for error in errors)


def test_backfill_accepts_a_duplicate_mapping_to_a_confirmed_family(monkeypatch, tmp_path) -> None:
    confirmed = _minimal_record()
    duplicate = _minimal_record(
        id="HBF-999-002",
        classification="duplicate",
        status="duplicate",
        severity="none",
        original_defect="same defect reported again",
    )
    _patch_backfill(monkeypatch, tmp_path, [confirmed, duplicate])
    assert prevention.validate_historical_defect_backfill() == []


# --------------------------------------------------------------------------
# Family-level protection layers are enforced, so catalogue drift cannot
# silently unguard a confirmed historical defect.
# --------------------------------------------------------------------------


def test_backfill_rejects_a_family_without_regression_coverage(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record()])
    _patch_registry(monkeypatch, tmp_path, [_family(regression=[])])
    errors = prevention.validate_historical_defect_backfill()
    assert any("families_without_regression" in error for error in errors)


def test_backfill_rejects_a_family_without_applicability_selector(monkeypatch, tmp_path) -> None:
    _patch_backfill(monkeypatch, tmp_path, [_minimal_record()])
    family = _family()
    family["applicability"] = {"changed_paths": [], "rationale": "none"}
    _patch_registry(monkeypatch, tmp_path, [family])
    errors = prevention.validate_historical_defect_backfill()
    assert any("families_without_selector" in error for error in errors)


def test_imported_families_preserve_selector_and_authority_boundaries() -> None:
    families, error = review.load_families()
    assert not error
    by_id = {family["id"]: family for family in families}

    malformed = "\u2060.github/workflows/ai-review.yml\u2060"
    assert "DFF-033" in review.applicable_family_ids(families, (malformed,))

    retry = by_id["DFF-034"]
    assert "overall dispatch deadline" in retry["invariant"]
    assert "Per-request timeouts" in retry["invariant"]

    verdict = by_id["DFF-037"]
    assert "mandatory" in verdict["invariant"]
    assert "Optional external-review" in verdict["invariant"]
    assert "DFF-026" in verdict["prevention"]["mechanism"]
