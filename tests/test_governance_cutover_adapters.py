from hunter.governance_cutover.adapters import DomainAdapter, decision_projection
from hunter.governance_cutover.shadow import ShadowRecorder

def test_adapter_records_parity_without_side_effect_surface(tmp_path):
    seen=[]
    def legacy(s):
        seen.append(("legacy", s["head_sha"])); return decision_projection("pending", "wait")
    def successor(s):
        seen.append(("successor", s["head_sha"])); return decision_projection("pending", "wait")
    obs=DomainAdapter("merge-readiness", legacy, successor).compare(ShadowRecorder(tmp_path), generation=1, head_sha="abc", snapshot={"head_sha":"abc"}, observed_at="t")
    assert obs.parity is True
    assert seen == [("legacy","abc"),("successor","abc")]

def test_adapter_exposes_real_divergence(tmp_path):
    a=DomainAdapter("governance", lambda _: decision_projection("failure","legacy"), lambda _: decision_projection("success","new"))
    assert a.compare(ShadowRecorder(tmp_path), generation=1, head_sha="abc", snapshot={}, observed_at="t").parity is False

def test_decision_projection_rejects_noncanonical_state():
    import pytest
    with pytest.raises(ValueError): decision_projection("clear", "bad")
