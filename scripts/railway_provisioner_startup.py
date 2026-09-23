#!/usr/bin/env python3
"""Railway runtime startup seam for the trusted provisioning edge (Issue #497).

This is the second Railway service backing the governed Issue-agent path: it
starts the repository-owned provisioning boundary
(``scripts/hunter_issue_agent_provisioner.py``) beside the read-only execution
issuer, over the same persistent ``/data`` evidence volume. It exists because
the GitHub Actions trigger -- which observes the owner's ``issues:labeled``
event and mints the signed authorization -- can never reach Railway's
``/data``, yet every dispatch must provision the per-Issue Source Handling
authority *before* the issuer is contacted.

Sequence
--------
1.  Read the evidence database path from the environment (the same
    ``HUNTER_ISSUE_AGENT_EVIDENCE_DB`` the issuer uses).
2.  Verify the evidence data directory is the mounted persistent Railway
    volume (same fail-closed check as the issuer seam).
3.  Invoke the existing canonical bootstrap through the shared public contract
    (``bootstrap_authority``): idempotently provision the operator root and
    genesis authorization rule before any per-Issue record can be derived.
4.  ``exec`` the canonical provisioner. Unlike the issuer seam, the Source
    Handling signing key is deliberately NOT scrubbed: this process is the
    designated trusted minting boundary and must hold the key for its whole
    lifetime so the automatic owner-label -> issuer path never needs a manual
    per-Issue provisioning step.

The read-only execution issuer remains unchanged and never holds the signing
key; the authority separation mandated by the architecture is preserved by
this separate trusted boundary.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

from railway_startup import (
    ensure_runtime_import_paths,
    require_evidence_database,
    require_signing_key,
    setup_logging,
)

_SCRIPTS_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("railway_provisioner_startup")


def _import_bootstrap():  # type: ignore[no-untyped-def]
    """Import the canonical bootstrap module from the scripts directory."""
    return importlib.import_module("bootstrap_source_handling_authority")


def _bootstrap(database: str) -> dict[str, object]:
    """Run the canonical Source Handling bootstrap against *database*."""
    bootstrap = _import_bootstrap()
    return bootstrap.bootstrap_authority(database, environ=os.environ)


def _exec_provisioner() -> None:
    """Replace the current process with the canonical provisioner.

    The signing key is intentionally left in the environment: the provisioner
    is the designated minting boundary and holds the key for its lifetime.
    """
    host = "0.0.0.0"
    port = os.environ.get("PORT", "8081")
    argv = [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_provisioner.py"),
        "--host",
        host,
        "--port",
        port,
    ]
    logger.info("launching provisioner: %s", " ".join(argv))
    os.execvpe(sys.executable, argv, os.environ)


def main() -> int:
    setup_logging()

    ensure_runtime_import_paths()

    database = require_evidence_database(environ=os.environ, logger=logger)
    if database is None:
        return 1

    if not require_signing_key(environ=os.environ, logger=logger, purpose="bootstrap and minting"):
        return 1

    try:
        outcome = _bootstrap(str(database))
    except Exception as error:  # noqa: BLE001 - fail closed before provisioner
        logger.error("bootstrap failed: %s", error)
        return 1

    logger.info(
        "bootstrap complete: status=%s operator_root=%s genesis=%s",
        outcome.get("status"),
        outcome.get("operator_root"),
        outcome.get("genesis_record_id"),
    )

    _exec_provisioner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
