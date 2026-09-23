from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "src/hunter/governance_cutover/authority.py"
SHADOW = ROOT / "src/hunter/governance_cutover/shadow.py"

REQUIRED_STATES = {
    "SHADOW_INSTALLED",
    "HISTORICAL_REPLAY_VERIFIED",
    "SHADOW_RUNTIME_VERIFIED",
    "CUTOVER_CANDIDATE",
    "LEGACY_AUTHORITY_DISABLED",
    "NEW_AUTHORITY_ENABLED",
    "POST_CUTOVER_VERIFIED",
    "LEGACY_CODE_REMOVABLE",
    "LEGACY_CODE_REMOVED",
    "CUTOVER_COMPLETE",
}


def validate_cutover_contract() -> list[str]:
    errors = []
    if not AUTHORITY.is_file() or not SHADOW.is_file():
        return ["production cutover authority and shadow modules must both exist"]
    source = AUTHORITY.read_text()
    # Enum assignments are AnnAssign-free regular Assign inside class; textual fallback keeps this guard simple and deterministic.
    missing = {state for state in REQUIRED_STATES if f'= "{state}"' not in source}
    if missing:
        errors.append(f"cutover state machine missing states: {sorted(missing)}")
    for symbol in ("publication_allowed", "fence_legacy", "fence_successor", "rollback", "install_shadow"):
        if f"def {symbol}(" not in source:
            errors.append(f"cutover authority missing {symbol}")
    shadow = SHADOW.read_text().lower()
    forbidden = ("requests", "urllib", "github", "transport", "subprocess", "socket", "statuses/", "workflow_dispatch")
    hits = sorted(token for token in forbidden if token in shadow)
    if hits:
        errors.append(f"shadow authority contains forbidden side-effect surfaces: {hits}")
    return errors


def main() -> int:
    errors = validate_cutover_contract()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Governance cutover contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
