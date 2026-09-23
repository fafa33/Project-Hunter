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

_SCRIPTS_DIR = Path(__file__).resolve().parent

_SIGNING_KEY_ENV = "HUNTER_SOURCE_HANDLING_SIGNING_KEY"
_EVIDENCE_DB_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
_RUNTIME_VENDOR_DIR_ENV = "HUNTER_RUNTIME_VENDOR_DIR"
_DEFAULT_VENDOR_DIR = "/app/vendor"

logger = logging.getLogger("railway_provisioner_startup")


def _runtime_vendor_dir() -> Path | None:
    """Return the configured pip ``--target`` install dir when it exists.

    Mirrors ``railway_issuer_startup``: the Railway build installs Hunter into
    ``/app/vendor`` (``pip install . --target /app/vendor``), so the deployed
    image does not carry Hunter in the interpreter's default site-packages.
    """
    configured = os.environ.get(_RUNTIME_VENDOR_DIR_ENV, _DEFAULT_VENDOR_DIR).strip()
    if not configured:
        return None
    vendor = Path(configured)
    return vendor if vendor.is_dir() else None


def _ensure_runtime_import_paths() -> None:
    """Expose the Railway runtime install dir to this process and its child."""
    vendor = _runtime_vendor_dir()
    if vendor is None:
        return
    vendor_path = str(vendor)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)
    existing = os.environ.get("PYTHONPATH", "")
    entries = [entry for entry in existing.split(os.pathsep) if entry and entry != vendor_path]
    os.environ["PYTHONPATH"] = os.pathsep.join([vendor_path, *entries])


def _import_bootstrap():  # type: ignore[no-untyped-def]
    """Import the canonical bootstrap module from the scripts directory."""
    return importlib.import_module("bootstrap_source_handling_authority")


def _evidence_volume_not_mounted(data_dir: Path) -> str | None:
    """Return an error message when *data_dir* cannot be shown to be a mounted volume."""
    if not data_dir.is_dir():
        return f"data directory {data_dir} does not exist; the Railway volume is not mounted"
    if not os.path.ismount(data_dir):
        return (
            f"data directory {data_dir} exists but is not a mounted Railway volume "
            "(ephemeral container storage); refusing to bootstrap into transient data"
        )
    return None


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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    _ensure_runtime_import_paths()

    database = os.environ.get(_EVIDENCE_DB_ENV, "").strip()
    if not database:
        logger.error(
            "required environment variable %s is not set; refusing to start",
            _EVIDENCE_DB_ENV,
        )
        return 1

    db_path = Path(database)
    data_dir = db_path.parent
    volume_error = _evidence_volume_not_mounted(data_dir)
    if volume_error is not None:
        logger.error("%s", volume_error)
        return 1

    if _SIGNING_KEY_ENV not in os.environ or not os.environ[_SIGNING_KEY_ENV].strip():
        logger.error(
            "required environment variable %s is not set; " "the signing key is needed for bootstrap and minting",
            _SIGNING_KEY_ENV,
        )
        return 1

    try:
        outcome = _bootstrap(database)
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
