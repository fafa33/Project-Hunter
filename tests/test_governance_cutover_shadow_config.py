import json
from pathlib import Path

def test_shadow_config_has_zero_mutation_authority():
    cfg=json.loads(Path("configs/governance_cutover_shadow.json").read_text())
    assert cfg["authoritative"] is False
    assert cfg["publication_allowed"] is False
    assert cfg["dispatch_allowed"] is False
    assert cfg["draft_mutation_allowed"] is False
    assert cfg["merge_allowed"] is False
    assert set(cfg["domains"]) == {"governance","candidate-admission","merge-readiness","review-orchestration","reviewer-collection"}
