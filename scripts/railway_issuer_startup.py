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
4.  Start the trusted provisioner child on its dedicated target port while the
    signing key is still present. The child and issuer share this service's one
    mounted evidence volume.
5.  Scrub ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from the parent environment so
    the long-running issuer never receives minting material.
6.  ``exec`` the canonical issuer with unchanged arguments; the current process
    is replaced so no Python wrapper lingers.

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
import shutil
import subprocess
import sys
from pathlib import Path

from railway_startup import (
    DEFAULT_VENDOR_DIR,
    EVIDENCE_DB_ENV,
    RUNTIME_VENDOR_DIR_ENV,
    SIGNING_KEY_ENV,
    ensure_runtime_import_paths,
    require_evidence_database,
    require_signing_key,
    scrub_signing_key,
    setup_logging,
)

_SCRIPTS_DIR = Path(__file__).resolve().parent

#: Backwards-compatible private aliases (shared state lives in railway_startup).
_SIGNING_KEY_ENV = SIGNING_KEY_ENV
_EVIDENCE_DB_ENV = EVIDENCE_DB_ENV
_RUNTIME_VENDOR_DIR_ENV = RUNTIME_VENDOR_DIR_ENV
_DEFAULT_VENDOR_DIR = DEFAULT_VENDOR_DIR
_ensure_runtime_import_paths = ensure_runtime_import_paths

_PROVENANCE_RESOLVER = "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"
_REPOSITORY_ENV = "HUNTER_ISSUE_AGENT_REPOSITORY"
_REPOSITORY_CHECKOUT_ENV = "HUNTER_ISSUE_AGENT_REPO_DIR"
_EXECUTION_BRANCH_ENV = "HUNTER_ISSUE_AGENT_EXECUTION_BRANCH"


def _canonical_github_remote(repository: str) -> str:
    """Return the credential-free GitHub URL for an owner/repository slug."""
    parts = repository.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise RuntimeError(f"{_REPOSITORY_ENV} must be an owner/repository slug")
    owner, name = parts
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if any(ch not in allowed for ch in owner + name):
        raise RuntimeError(f"{_REPOSITORY_ENV} contains invalid GitHub repository characters")
    return f"https://github.com/{owner}/{name}.git"


def _prepare_repository_checkout() -> None:
    """Materialize and verify the configured execution checkout before issuer composition.

    Railway images contain installed Hunter code, not a writable Git checkout.  The
    fallback runtime deliberately requires a real checkout with a credential-free
    pinned GitHub origin.  Startup therefore owns this deployment concern rather
    than relying on dashboard shell patches.
    """
    repository = os.environ.get(_REPOSITORY_ENV, "").strip()
    checkout_raw = os.environ.get(_REPOSITORY_CHECKOUT_ENV, "").strip()
    branch = os.environ.get(_EXECUTION_BRANCH_ENV, "").strip()
    configured = (bool(repository), bool(checkout_raw), bool(branch))
    if not any(configured):
        # Unit/bootstrap-only invocations do not compose the issuer. The issuer
        # itself still requires all three variables; production supplies them.
        return
    if not all(configured):
        raise RuntimeError("repository checkout configuration is incomplete")
    checkout = Path(checkout_raw).resolve()
    remote = _canonical_github_remote(repository)
    if (
        checkout == Path("/")
        or checkout == Path("/app")
        or "/data" == str(checkout)
        or str(checkout).startswith("/data/")
    ):
        raise RuntimeError("repository checkout must use disposable runtime storage")
    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ("git", "clone", "--no-tags", "--single-branch", "--branch", branch, remote, str(checkout)),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("failed to materialize configured execution checkout")
    pinned = subprocess.run(
        ("git", "remote", "get-url", "origin"),
        cwd=checkout,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if pinned.returncode != 0 or pinned.stdout.strip() != remote:
        raise RuntimeError("execution checkout origin is not the canonical credential-free GitHub remote")
    logger.info("execution checkout prepared for %s on branch %s", repository, branch)


logger = logging.getLogger("railway_issuer_startup")


def _import_bootstrap():  # type: ignore[no-untyped-def]
    """Import the canonical bootstrap module from the scripts directory.

    ``importlib.import_module`` returns the cached module from ``sys.modules``
    on repeat calls, so the import itself is the whole idempotent contract.
    """
    return importlib.import_module("bootstrap_source_handling_authority")


def _bootstrap(database: str) -> dict[str, object]:
    """Run the canonical Source Handling bootstrap against *database*.

    Returns the parsed JSON outcome on success.  Raises on any failure so the
    caller can ``fail-closed`` before the issuer is started.
    """
    bootstrap = _import_bootstrap()
    return bootstrap.bootstrap_authority(database, environ=os.environ)


def _scrub_signing_key() -> None:
    """Remove ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from the process env."""
    scrub_signing_key()


def _start_provisioner() -> subprocess.Popen[bytes]:
    """Start the trusted provisioning edge inside this volume-owning service.

    The child is forked before the parent scrubs the Source Handling signing key,
    so only the provisioner retains minting material.  Both edges therefore see
    the same service-scoped /data volume without pretending Railway can attach
    one volume to two services.
    """
    host = "0.0.0.0"
    port = os.environ.get("HUNTER_ISSUE_AGENT_PROVISIONER_PORT", "8081")
    argv = [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_provisioner.py"),
        "--host",
        host,
        "--port",
        port,
    ]
    logger.info("launching trusted provisioner on port %s", port)
    return subprocess.Popen(argv, env=os.environ.copy())


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
    setup_logging()

    ensure_runtime_import_paths()

    database = require_evidence_database(environ=os.environ, logger=logger)
    if database is None:
        return 1

    if not require_signing_key(environ=os.environ, logger=logger, purpose="bootstrap"):
        return 1

    try:
        outcome = _bootstrap(str(database))
    except Exception as error:  # noqa: BLE001 - fail closed before issuer
        logger.error("bootstrap failed: %s", error)
        return 1

    logger.info(
        "bootstrap complete: status=%s operator_root=%s genesis=%s",
        outcome.get("status"),
        outcome.get("operator_root"),
        outcome.get("genesis_record_id"),
    )

    try:
        _prepare_repository_checkout()
    except Exception as error:  # noqa: BLE001 - fail closed before issuer
        logger.error("repository checkout preparation failed: %s", error)
        return 1

    provisioner = _start_provisioner()
    if provisioner.poll() is not None:
        logger.error("trusted provisioner exited during startup")
        return 1

    _scrub_signing_key()
    logger.info("signing key scrubbed from issuer environment")

    _exec_issuer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
