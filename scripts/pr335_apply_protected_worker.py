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


if __name__ == "__main__":
    main()
