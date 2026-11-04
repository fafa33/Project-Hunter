"""Credential-free, independent read-only verifier of the Kilo gateway canary.

This is NOT the production Trusted Validator: it has no signed lifecycle ledger.
"""

from __future__ import annotations

import hashlib
import json
import sys

from container_broker_canary import IMAGE, docker

EXPECTED = b"def test_sample():\n    assert 2 + 2 == 4\n"


def validate(volume: str, broker: str) -> dict[str, str]:
    if not volume.startswith("hunter-682-kilo-work-") or not broker.startswith("hunter-682-kilo-broker-"):
        raise ValueError("unexpected canary identity")
    result = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65532",
        "--mount",
        f"type=volume,src={volume},dst=/workspace,readonly",
        IMAGE,
        "python",
        "-c",
        "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path('/workspace/test_sample.py').read_bytes())",
    )
    if result.returncode or result.stdout.encode() != EXPECTED:
        raise ValueError("candidate differs from expected repaired content")
    logs = docker("logs", broker)
    lines = [line for line in logs.stdout.splitlines() if line.startswith("BROKER_EXECUTED:")]
    if len(lines) != 2 or not lines[0].endswith("EXIT: 1") or not lines[1].endswith("EXIT: 0"):
        raise ValueError("independent broker test sequence invalid")
    return {
        "verdict": "PASS",
        "content_sha256": hashlib.sha256(EXPECTED).hexdigest(),
        "broker_test_sequence": "FAIL_THEN_PASS",
        "scope": "CANARY_ONLY_NOT_PRODUCTION_TRUSTED_VALIDATOR",
    }


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1], sys.argv[2]), sort_keys=True))
