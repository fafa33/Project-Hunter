#!/usr/bin/env python3
"""Railway runtime startup seam: volume-aware bootstrap then issuer launch.

This is the single canonical Railway start command (Issue #442).  It replaces
the direct issuer invocation so the persistent ``/data`` volume is
idempotently bootstrapped at runtime -- *after* the Railway volume is mounted
but *before* the long-running issuer process starts.

Sequence
--------
1.  Read the evidence database path from the environment (the same path the
    issuer will use: ``HUNTER_ISSUE_AGENT_EVIDENCE_DB``, typically
    ``/data/evidence.sqlite``).
2.  Invoke the existing canonical bootstrap
    (``bootstrap_source_handling_authority``) which provisions the operator
    root and genesis authorization rule.  On a fresh volume this writes the
    authority; on an already-bootstrapped volume the idempotency check passes
    through without mutation; on a tampered or mismatched volume the bootstrap
    fails closed before any issuer state is composed.
3.  Scrub ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from the process environment
    so the long-running steady-state issuer never retains bootstrap-only
    signing material.
4.  ``exec`` the canonical issuer with unchanged arguments; the current
    process is replaced so no Python wrapper lingers.

Design constraints
------------------
-   Repository-owned: the start command is pinned in ``railway.toml``; no
    dashboard-only configuration bypasses it.
-   Fail-closed: any bootstrap inconsistency, missing volume, missing signing
    key, or misconfiguration terminates the process before the issuer starts.
-   Idempotent: repeated restarts on an already-bootstrapped volume are
    deterministic no-ops.
-   Security: the signing key is consumed by the bootstrap and never reaches
    the issuer process environment.
-   No parallel mechanism: this script delegates entirely to the existing
    canonical bootstrap and issuer scripts.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent

_SIGNING_KEY_ENV = "HUNTER_SOURCE_HANDLING_SIGNING_KEY"
_EVIDENCE_DB_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"

_PROVENANCE_RESOLVER = "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"

logger = logging.getLogger("railway_issuer_startup")


def _import_bootstrap():  # type: ignore[no-untyped-def]
    """Import the canonical bootstrap module from the scripts directory."""
    if "bootstrap_source_handling_authority" not in sys.modules:
        importlib.import_module("bootstrap_source_handling_authority")
    return sys.modules["bootstrap_source_handling_authority"]


def _bootstrap(database: str) -> dict[str, object]:
    """Run the canonical Source Handling bootstrap against *database*.

    Returns the parsed JSON outcome on success.  Raises on any failure so the
    caller can ``fail-closed`` before the issuer is started.
    """
    bootstrap = _import_bootstrap()

    signing_key = bootstrap._load_signing_key(  # type: ignore[attr-defined]
        environ=os.environ,
        signing_key_file=None,
    )
    rule = bootstrap._load_production_rule()  # type: ignore[attr-defined]
    return bootstrap._run(database, signing_key, rule)  # type: ignore[attr-defined]


def _scrub_signing_key() -> None:
    """Remove ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from the process env."""
    os.environ.pop(_SIGNING_KEY_ENV, None)


def _exec_issuer() -> None:
    """Replace the current process with the canonical issuer.

    Port is read from ``$PORT`` (provided by Railway) so the repository-owned
    ``railway.toml`` need not hard-code it.
    """
    host = "0.0.0.0"
    port = os.environ.get("PORT", "8080")
    argv = [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_issuer.py"),
        "--host",
        host,
        "--port",
        port,
        "--provenance-resolver",
        _PROVENANCE_RESOLVER,
    ]
    logger.info("launching issuer: %s", " ".join(argv))
    os.execvpe(sys.executable, argv, os.environ)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    database = os.environ.get(_EVIDENCE_DB_ENV, "").strip()
    if not database:
        logger.error(
            "required environment variable %s is not set; refusing to start",
            _EVIDENCE_DB_ENV,
        )
        return 1

    db_path = Path(database)
    data_dir = db_path.parent
    if not data_dir.exists():
        logger.error(
            "data directory %s does not exist; the Railway volume is not mounted",
            data_dir,
        )
        return 1

    if _SIGNING_KEY_ENV not in os.environ or not os.environ[_SIGNING_KEY_ENV].strip():
        logger.error(
            "required environment variable %s is not set; " "the signing key is needed for bootstrap",
            _SIGNING_KEY_ENV,
        )
        return 1

    try:
        outcome = _bootstrap(database)
    except Exception as error:  # noqa: BLE001 - fail closed before issuer
        logger.error("bootstrap failed: %s", error)
        return 1

    logger.info(
        "bootstrap complete: status=%s operator_root=%s genesis=%s",
        outcome.get("status"),
        outcome.get("operator_root"),
        outcome.get("genesis_record_id"),
    )

    _scrub_signing_key()
    logger.info("signing key scrubbed from environment")

    _exec_issuer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
