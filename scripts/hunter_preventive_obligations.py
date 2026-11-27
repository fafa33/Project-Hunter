"""Standalone CLI for the repository-owned preventive-obligation checks.

The implementation lives in ``hunter_ci_impact.py`` so the existing push
boundary consumes it through an already-authorized root-of-trust dependency.
This module is only a convenience entry point and is not a separate gate.
"""

from __future__ import annotations

from hunter_ci_impact import (
    applicable_obligations,
    enforce_preventive_obligations,
    evidence_digest,
    execute_obligations,
    load_obligations,
    obligations_main,
)

__all__ = [
    "applicable_obligations",
    "enforce_preventive_obligations",
    "evidence_digest",
    "execute_obligations",
    "load_obligations",
    "obligations_main",
]


if __name__ == "__main__":
    raise SystemExit(obligations_main())
