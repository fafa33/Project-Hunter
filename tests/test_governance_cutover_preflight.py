from __future__ import annotations

import hunter_governance_cutover_preflight as gate


def test_cutover_contract_is_machine_guarded():
    assert gate.validate_cutover_contract() == []


def test_shadow_side_effect_regression_is_rejected(tmp_path, monkeypatch):
    shadow = tmp_path / "shadow.py"
    shadow.write_text("import requests\n")
    monkeypatch.setattr(gate, "SHADOW", shadow)
    errors = gate.validate_cutover_contract()
    assert any("forbidden side-effect" in error for error in errors)
