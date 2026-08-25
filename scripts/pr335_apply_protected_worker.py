from __future__ import annotations

import subprocess
from pathlib import Path

SOURCE_COMMIT = "03fc330da15eb64451950977af849b3a0d184607"
SOURCE_PATH = ".github/workflows/pr335-isolation-fix.yml"


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
    shell_path = Path("/tmp/pr335-apply-protected-worker.sh")
    shell_path.write_text("\n".join(body) + "\n")
    subprocess.run(["bash", str(shell_path)], check=True)

    tests = Path("tests/test_response_validator_phase_b.py")
    text = tests.read_text()
    old = """    consumer = harness.validator._ResponseValidator__transient_response_consumer  # noqa: SLF001
    coordinates = authorization.coordinates
"""
    new = """    boundary = harness.transient_response_vault
    coordinates = authorization.coordinates
"""
    if old not in text:
        raise RuntimeError("body-loss regression still expected old consumer reference")
    text = text.replace(old, new, 1)
    old = """    consumer.discard_authorized(
"""
    new = """    boundary.discard_authorized(
"""
    if old not in text:
        raise RuntimeError("body-loss regression discard call not found")
    tests.write_text(text.replace(old, new, 1))


if __name__ == "__main__":
    main()
