#!/usr/bin/env python3
"""Railway runtime startup seam: volume-aware bootstrap then issuer launch.

This is the single canonical Railway start command (Issue #442).  It replaces
the direct issuer invocation so the persistent ``/data`` volume is
idempotently bootstrapped at runtime -- *after* the Railway volume is mounted
but *before* the long-running issuer process starts.

Sequence
-------
1.  Read the evidence database path from the environment (the same path the
    issuer will use: ``HUNTER_ISSUE_AGENT_EVIDENCE_DB``, typically
    ``/data/evidence.sqlite``).
2.  Verify the evidence data directory is the mounted persistent Railway
    volume, not merely a directory that exists.  A directory baked into the
    image or created by a build step is ephemeral container storage; booting
    there would silently lose authority on the next restart, so the seam fails
    closed instead.
3.  Invoke the existing canonical bootstrap through the shared public contract
    (``bootstrap_source_handling_authority.bootstrap_authority``), which
    provisions the operator root and genesis authorization rule.  On a fresh
    volume this writes the authority; on an already-bootstrapped volume the
    idempotency check passes through without mutation; on a tampered or
    mismatched volume the bootstrap fails closed before any issuer state is
    composed.
4.  Scrub ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from the process environment
    so the long-running steady-state issuer never retains bootstrap-only
    signing material.
5.  Select the repository-owned Railway OpenCode sandbox launcher by default.
6.  ``exec`` the canonical issuer with unchanged arguments; the current
    process is replaced so no Python wrapper lingers.

Design constraints
------------------
-   Repository-owned: the start command is pinned in ``railway.toml``; no
    dashboard-only configuration bypasses it.
-   Fail-closed: a missing or non-mounted volume, missing signing key,
    bootstrap inconsistency, or any other misconfiguration terminates the
    process before the issuer starts; a merely-existing directory is never
    accepted as proof of a mounted volume.
-   Idempotent: repeated restarts on an already-bootstrapped volume are
    deterministic no-ops.
-   Security: the signing key is consumed by the bootstrap and never reaches
    the issuer process environment.
-   No parallel mechanism: this script delegates entirely to the existing
    canonical bootstrap and issuer scripts, sharing the bootstrap module's
    single public ``bootstrap_authority`` contract with the operator CLI.
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
_OPENCODE_SANDBOX_ENV = "HUNTER_OPENCODE_SANDBOX_EXECUTABLE"
_DEFAULT_VENDOR_DIR = "/app/vendor"
_DEFAULT_OPENCODE_SANDBOX_EXECUTABLE = "/app/bin/hunter-railway-opencode-sandbox"

_PROVENANCE_RESOLVER = "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"

logger = logging.getLogger("railway_issuer_startup")


def _runtime_vendor_dir() -> Path | None:
    """Return the configured pip ``--target`` install dir when it exists.

    The Railway build installs Hunter into ``/app/vendor`` (``pip install . --
    target /app/vendor``), so the deployed image does not carry Hunter in the
    interpreter's default site-packages.  The start command must make that
    directory importable both for this seam and for the issuer process it
    ``exec``s.
    """
    configured = os.environ.get(_RUNTIME_VENDOR_DIR_ENV, _DEFAULT_VENDOR_DIR).strip()
    if not configured:
        return None
    vendor = Path(configured)
    return vendor if vendor.is_dir() else None


def _ensure_runtime_import_paths() -> None:
    """Expose the Railway runtime install dir to this process and its issuer child.

    The module-freezing import of ``bootstrap_source_handling_authority`` (and
    the ``hunter`` package it imports) happens lazily in the seam, while the
    issuer is ``exec``ed into a fresh interpreter.  Both need the pip
    ``--target`` directory on the import path: this process via ``sys.path``
    and the child via an exported ``PYTHONPATH``.
    """
    vendor = _runtime_vendor_dir()
    if vendor is None:
        return
    vendor_path = str(vendor)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)
    existing = os.environ.get("PYTHONPATH", "")
    entries = [entry for entry in existing.split(os.pathsep) if entry and entry != vendor_path]
    os.environ["PYTHONPATH"] = os.pathsep.join([vendor_path, *entries])


def _ensure_opencode_sandbox_executable() -> None:
    """Select the repository-owned Railway sandbox launcher unless overridden.

    Railway cannot create the namespaces required by bubblewrap.  The build
    installs a constrained executable launcher at a fixed path; publishing that
    path through the canonical provider environment ensures the deployed runtime
    selects the Railway-safe shim instead of silently falling back to ``bwrap``.
    An explicit non-empty operator override is preserved.
    """
    configured = os.environ.get(_OPENCODE_SANDBOX_ENV, "").strip()
    if configured:
        return
    os.environ[_OPENCODE_SANDBOX_ENV] = _DEFAULT_OPENCODE_SANDBOX_EXECUTABLE


def _import_bootstrap():  # type: ignore[no-untyped-def]
    """Import the canonical bootstrap module from the scripts directory.

    ``importlib.import_module`` returns the cached module from ``sys.modules``
    on repeat calls, so the import itself is the whole idempotent contract.
    """
    return importlib.import_module("bootstrap_source_handling_authority")


def _evidence_volume_not_mounted(data_dir: Path) -> str | None:
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


def _bootstrap(database: str) -> dict[str, object]:
    """Run the canonical Source Handling bootstrap against *database*.

    Returns the parsed JSON outcome on success.  Raises on any failure so the
    caller can ``fail-closed`` before the issuer is started.
    """
    bootstrap = _import_bootstrap()
    return bootstrap.bootstrap_authority(database, environ=os.environ)


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

    _ensure_runtime_import_paths()
    _ensure_opencode_sandbox_executable()

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
