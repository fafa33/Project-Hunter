#!/usr/bin/env python3
"""Shared Railway deployment-seam plumbing for the Issue-agent services (Issue #497).

The issuer startup seam (``scripts/railway_issuer_startup.py``) and the trusted
provisioning boundary seam (``scripts/railway_provisioner_startup.py``) both
launch a governed Issue-agent service over the same persistent ``/data`` volume.
Both must fail closed unless the evidence database is set in the environment,
its data directory is a mounted Railway volume, and the operator-provenance
signing key is available. This module is the single home for that shared
environment validation, the runtime vendor import-path wiring, and key
scrubbing, so the two seams cannot drift apart on a security-relevant preflight.

No authority is ever resolved here: bootstrap delegation, ``exec`` and the
key-scrubbing policy stay in each seam, so the issuer keeps scrubbing the key
while the provisioner keeps it for its minting lifetime.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

SIGNING_KEY_ENV = "HUNTER_SOURCE_HANDLING_SIGNING_KEY"
EVIDENCE_DB_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
RUNTIME_VENDOR_DIR_ENV = "HUNTER_RUNTIME_VENDOR_DIR"
DEFAULT_VENDOR_DIR = "/app/vendor"


def runtime_vendor_dir() -> Path | None:
    """Return the configured pip ``--target`` install dir when it exists.

    The Railway build installs Hunter into ``/app/vendor`` (``pip install . --
    target /app/vendor``), so the deployed image does not carry Hunter in the
    interpreter's default site-packages.  The start command must make that
    directory importable both for the seam and for the child it ``exec``s.
    """
    configured = os.environ.get(RUNTIME_VENDOR_DIR_ENV, DEFAULT_VENDOR_DIR).strip()
    if not configured:
        return None
    vendor = Path(configured)
    return vendor if vendor.is_dir() else None


def ensure_runtime_import_paths() -> None:
    """Expose the Railway runtime install dir to this process and its child."""
    vendor = runtime_vendor_dir()
    if vendor is None:
        return
    vendor_path = str(vendor)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)
    existing = os.environ.get("PYTHONPATH", "")
    entries = [entry for entry in existing.split(os.pathsep) if entry and entry != vendor_path]
    os.environ["PYTHONPATH"] = os.pathsep.join([vendor_path, *entries])


def evidence_volume_not_mounted(data_dir: Path) -> str | None:
    """Return an error message when *data_dir* cannot be shown to be a mounted volume.

    A directory that merely exists is not proof that Railway mounted the
    persistent volume: the path could have been created by a build step or
    baked into the image, and bootstrapping there would silently write into
    ephemeral container storage that is lost on the next restart.  The seam
    fails closed unless the evidence directory is a real mount point.
    """
    if not data_dir.is_dir():
        return f"data directory {data_dir} does not exist; the Railway volume is not mounted"
    if not os.path.ismount(data_dir):
        return (
            f"data directory {data_dir} exists but is not a mounted Railway volume "
            "(ephemeral container storage); refusing to bootstrap into transient data"
        )
    return None


def require_evidence_database(environ: Mapping[str, str], *, logger: logging.Logger) -> Path | None:
    """Return the validated evidence database path, or ``None`` after logging failure.

    The database environment must be present and its parent data directory must
    be a mounted persistent volume; anything else fails closed before any
    bootstrap can run.
    """
    database = environ.get(EVIDENCE_DB_ENV, "").strip()
    if not database:
        logger.error("required environment variable %s is not set; refusing to start", EVIDENCE_DB_ENV)
        return None
    db_path = Path(database)
    volume_error = evidence_volume_not_mounted(db_path.parent)
    if volume_error is not None:
        logger.error("%s", volume_error)
        return None
    return db_path


def require_signing_key(environ: Mapping[str, str], *, logger: logging.Logger, purpose: str) -> bool:
    """Return whether the Source Handling signing key is present, logging failure.

    ``purpose`` states why the key is required at that seam (``"bootstrap"`` for
    the issuer, ``"bootstrap and minting"`` for the provisioning boundary).
    """
    if SIGNING_KEY_ENV not in environ or not environ[SIGNING_KEY_ENV].strip():
        logger.error(
            "required environment variable %s is not set; the signing key is needed for %s",
            SIGNING_KEY_ENV,
            purpose,
        )
        return False
    return True


def scrub_signing_key() -> None:
    """Remove the Source Handling signing key from the process environment."""
    os.environ.pop(SIGNING_KEY_ENV, None)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure process-wide logging for one startup seam."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
