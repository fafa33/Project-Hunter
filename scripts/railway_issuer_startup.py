#!/usr/bin/env python3
"""Railway runtime startup seam: volume-aware bootstrap then governed edge topology.

This is the single canonical Railway start command (Issue #442).  It replaces
the direct issuer invocation so the persistent ``/data`` volume is
idempotently bootstrapped at runtime -- *after* the Railway volume is mounted
but *before* the long-running issuer process starts.

Sequence
--------
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
3a. Prepare the empty per-authorization workspace root; nothing is cloned
    (``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` I3).
3b. When the OpenCode provider is configured, run its self-check with the
    issuer's environment: a forbidden shell attempt must be rejected, or
    nothing starts (contract I8).
4.  Resolve one port plan: Railway's public ``$PORT`` plus two distinct
    internal ports (``HUNTER_ISSUE_AGENT_PROVISIONER_PORT``, default 8081, and
    ``HUNTER_ISSUE_AGENT_ISSUER_PORT``, default 8082).  Any collision fails
    closed.
5.  Start the trusted provisioner child on ``127.0.0.1`` at its internal port
    while the signing key is still present; it is the only child whose
    environment carries the key.
6.  Scrub ``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` from this process's
    environment, then start the issuer child on ``127.0.0.1`` at its internal
    port from the scrubbed environment.
7.  Start the public ingress child (``hunter_issue_agent_ingress.py``) on
    ``0.0.0.0:$PORT`` with an allowlisted environment that carries no secret.
    It is the only listener Railway's single public domain reaches, and it
    routes exactly ``POST /issue-agent/provision`` to the provisioner,
    ``POST /issue-agent/authorize`` to the issuer, and ``GET /healthz`` to a
    health answer that requires both internal authorities.
8.  Supervise: if any child exits, stop the others and exit non-zero so
    Railway restarts the whole topology rather than serving half of it.  On
    SIGTERM/SIGINT stop the children (ingress first) and exit.

Design constraints
------------------
-   Repository-owned: the start command is pinned in ``railway.toml``; no
    dashboard-only configuration bypasses it, and no second Railway domain or
    target port is required -- one public port serves both canonical paths.
-   Fail-closed: a missing or non-mounted volume, missing signing key,
    bootstrap inconsistency, port collision, child launch failure or any other
    misconfiguration terminates the process; a merely-existing directory is
    never accepted as proof of a mounted volume.
-   Idempotent: repeated restarts on an already-bootstrapped volume are
    deterministic no-ops.
-   Security: the signing key reaches only the provisioner child.  The issuer
    and the public ingress are launched after it is scrubbed, and the internal
    authorities are bound to loopback so neither is reachable except through
    the ingress's fixed route table.
-   No parallel mechanism: this script delegates entirely to the existing
    canonical bootstrap, provisioner and issuer scripts, sharing the bootstrap
    module's single public ``bootstrap_authority`` contract with the operator
    CLI.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
_DISPOSABLE_CHECKOUT_ROOT = Path("/app/.hunter-runtime-checkouts")

_PUBLIC_PORT_ENV = "PORT"
_PROVISIONER_PORT_ENV = "HUNTER_ISSUE_AGENT_PROVISIONER_PORT"
_ISSUER_PORT_ENV = "HUNTER_ISSUE_AGENT_ISSUER_PORT"
_DEFAULT_PUBLIC_PORT = "8080"
_DEFAULT_PROVISIONER_PORT = "8081"
_DEFAULT_ISSUER_PORT = "8082"

#: Internal authorities bind loopback only; only the ingress binds publicly.
INTERNAL_HOST = "127.0.0.1"
PUBLIC_HOST = "0.0.0.0"

#: The public ingress needs no secret; it is launched with only these variables.
INGRESS_ENVIRONMENT_ALLOWLIST = ("PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED")

_SUPERVISOR_POLL_SECONDS = 0.5
_CHILD_STOP_TIMEOUT_SECONDS = 30.0

#: Children the supervisor never kills on a deadline.  The issuer runs accepted
#: authorizations on non-daemon workers and stays alive until each reaches a
#: durable terminal ledger outcome; only the platform's own stop grace period
#: may bound that drain, exactly as when the issuer was the container's process.
_DRAIN_WITHOUT_DEADLINE = frozenset({"issuer"})


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


def _prepare_workspace_root() -> None:
    """Prepare the empty root beneath which each authorization gets its own workspace.

    ``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` I3: nothing is cloned at startup
    and no branch is configured. Every execution materializes its own isolated
    workspace at its signed base, on its derived branch, after dispatch. Startup
    only guarantees that the configured root is a disposable directory beneath
    the approved checkout root and that no workspace from a previous process
    survives into this one.
    """
    repository = os.environ.get(_REPOSITORY_ENV, "").strip()
    root_raw = os.environ.get(_REPOSITORY_CHECKOUT_ENV, "").strip()
    if os.environ.get(_EXECUTION_BRANCH_ENV, "").strip():
        logger.warning(
            "%s is retired and ignored: each authorization executes on the branch derived from its "
            "signed scope; remove it from the deployment",
            _EXECUTION_BRANCH_ENV,
        )
    configured = (bool(repository), bool(root_raw))
    if not any(configured):
        # Unit/bootstrap-only invocations do not compose the issuer. The issuer
        # itself still requires both variables; production supplies them.
        return
    if not all(configured):
        raise RuntimeError("issue agent workspace configuration is incomplete")
    _canonical_github_remote(repository)
    root = Path(root_raw).resolve()
    disposable_root = _DISPOSABLE_CHECKOUT_ROOT.resolve()
    if root == disposable_root or disposable_root not in root.parents:
        raise RuntimeError(f"issue agent workspace root must be contained beneath {disposable_root}")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    logger.info("issue agent workspace root prepared for %s", repository)


#: The governed OpenCode provider command; when configured, it must pass the
#: startup self-check before any child of the topology is launched.
_OPENCODE_COMMAND_ENV = "HUNTER_AGENT_OPENCODE_COMMAND"
_PROVIDER_SELF_CHECK_MODULE = "hunter.automation.opencode_provider_self_check"
_PROVIDER_SELF_CHECK_TIMEOUT_SECONDS = 900


def _run_provider_self_check() -> None:
    """Prove the governed provider rejects a forbidden shell attempt, or fail closed.

    ``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` I8. The probe runs with the
    issuer's own environment (never the Source Handling signing key), through
    the same sandbox launcher and permission contract as a real execution.
    """
    if not os.environ.get(_OPENCODE_COMMAND_ENV, "").strip():
        # Unit/bootstrap-only invocations configure no provider pool.
        return
    completed = subprocess.run(
        (sys.executable, "-m", _PROVIDER_SELF_CHECK_MODULE),
        env=issuer_environment(os.environ),
        check=False,
        timeout=_PROVIDER_SELF_CHECK_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError("the governed OpenCode provider failed its startup self-check")
    logger.info("governed OpenCode provider passed its startup self-check")


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


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    """The one public port and the two distinct loopback-only internal ports."""

    public: int
    provisioner: int
    issuer: int


def _parse_port(name: str, raw: str) -> int:
    value = raw.strip()
    if not (value.isascii() and value.isdecimal()) or not 1 <= int(value) <= 65535:
        raise RuntimeError(f"{name} must be a TCP port in 1..65535")
    return int(value)


def resolve_runtime_ports(environ: Mapping[str, str]) -> RuntimePorts:
    """Resolve and validate the port plan; any collision fails closed."""
    ports = RuntimePorts(
        public=_parse_port(_PUBLIC_PORT_ENV, environ.get(_PUBLIC_PORT_ENV, _DEFAULT_PUBLIC_PORT)),
        provisioner=_parse_port(_PROVISIONER_PORT_ENV, environ.get(_PROVISIONER_PORT_ENV, _DEFAULT_PROVISIONER_PORT)),
        issuer=_parse_port(_ISSUER_PORT_ENV, environ.get(_ISSUER_PORT_ENV, _DEFAULT_ISSUER_PORT)),
    )
    if len({ports.public, ports.provisioner, ports.issuer}) != 3:
        raise RuntimeError(
            f"{_PUBLIC_PORT_ENV}, {_PROVISIONER_PORT_ENV} and {_ISSUER_PORT_ENV} must be three distinct ports"
        )
    return ports


def provisioner_argv(ports: RuntimePorts) -> list[str]:
    """The trusted provisioner listens on loopback only."""
    return [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_provisioner.py"),
        "--host",
        INTERNAL_HOST,
        "--port",
        str(ports.provisioner),
    ]


def issuer_argv(ports: RuntimePorts) -> list[str]:
    """The canonical issuer invocation, listening on loopback only."""
    return [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_issuer.py"),
        "--host",
        INTERNAL_HOST,
        "--port",
        str(ports.issuer),
        "--provenance-resolver",
        _PROVENANCE_RESOLVER,
    ]


def ingress_argv(ports: RuntimePorts) -> list[str]:
    """The public ingress owns Railway's ``$PORT`` and nothing else."""
    return [
        sys.executable,
        str(_SCRIPTS_DIR / "hunter_issue_agent_ingress.py"),
        "--host",
        PUBLIC_HOST,
        "--port",
        str(ports.public),
        "--provisioner-port",
        str(ports.provisioner),
        "--issuer-port",
        str(ports.issuer),
    ]


def provisioner_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The provisioner is the minting boundary: it alone receives the signing key."""
    if not environ.get(SIGNING_KEY_ENV, "").strip():
        raise RuntimeError("the trusted provisioner requires the Source Handling signing key")
    return dict(environ)


def issuer_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The issuer's environment never carries the Source Handling signing key."""
    environment = dict(environ)
    environment.pop(SIGNING_KEY_ENV, None)
    return environment


def ingress_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The public ingress holds no authority: allowlisted, secret-free variables only."""
    return {name: environ[name] for name in INGRESS_ENVIRONMENT_ALLOWLIST if name in environ}


def _spawn(role: str, argv: list[str], env: dict[str, str]) -> Any:
    """Launch one child of the governed topology (the single launch seam)."""
    logger.info("launching %s: %s", role, " ".join(argv))
    return subprocess.Popen(argv, env=env)


def _stop_children(children: list[tuple[str, Any]]) -> None:
    """Stop children in reverse launch order: ingress first, provisioner last.

    Every child is signalled first, so the ingress stops admitting before the
    issuer drains.  The issuer is waited on without a deadline (see
    ``_DRAIN_WITHOUT_DEADLINE``); the ingress and provisioner hold no accepted
    long-running work and are killed if they outlive the stop timeout.
    """
    for _role, child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for role, child in reversed(children):
        if role in _DRAIN_WITHOUT_DEADLINE:
            child.wait()
            continue
        try:
            child.wait(timeout=_CHILD_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.error("%s did not stop within %.0fs; killing", role, _CHILD_STOP_TIMEOUT_SECONDS)
            child.kill()
            child.wait(timeout=_CHILD_STOP_TIMEOUT_SECONDS)


def _launch_topology(ports: RuntimePorts) -> list[tuple[str, Any]]:
    """Start the provisioner (with key), scrub, then the issuer and ingress (without).

    Any launch failure stops every child already started and re-raises, so a
    partial topology never keeps running.
    """
    children: list[tuple[str, Any]] = []
    try:
        children.append(
            ("provisioner", _spawn("provisioner", provisioner_argv(ports), provisioner_environment(os.environ)))
        )
        _scrub_signing_key()
        if SIGNING_KEY_ENV in os.environ:
            raise RuntimeError("signing key still present after scrub")
        logger.info("signing key scrubbed before launching issuer and ingress")
        children.append(("issuer", _spawn("issuer", issuer_argv(ports), issuer_environment(os.environ))))
        children.append(("ingress", _spawn("ingress", ingress_argv(ports), ingress_environment(os.environ))))
        for role, child in children:
            if child.poll() is not None:
                raise RuntimeError(f"{role} exited during startup")
    except BaseException:
        _scrub_signing_key()
        _stop_children(children)
        raise
    return children


def supervise(
    children: list[tuple[str, Any]],
    stop: threading.Event,
    *,
    poll_interval: float = _SUPERVISOR_POLL_SECONDS,
) -> int:
    """Run until stopped or until any child exits; never leave half a topology."""
    exit_code = 0
    while not stop.wait(poll_interval):
        exited = [(role, child.poll()) for role, child in children if child.poll() is not None]
        if exited:
            for role, code in exited:
                logger.error("%s exited with status %s; stopping the governed topology", role, code)
            exit_code = 1
            break
    _stop_children(children)
    return exit_code


def _install_stop_signals(stop: threading.Event) -> None:
    def _handler(signum: int, frame: Any) -> None:
        logger.info("received signal %d, stopping governed topology", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


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
        _prepare_workspace_root()
    except Exception as error:  # noqa: BLE001 - fail closed before issuer
        logger.error("issue agent workspace preparation failed: %s", error)
        return 1

    try:
        _run_provider_self_check()
    except Exception as error:  # noqa: BLE001 - fail closed before issuer
        logger.error("provider self-check failed: %s", error)
        return 1

    try:
        ports = resolve_runtime_ports(os.environ)
    except RuntimeError as error:
        logger.error("port configuration invalid: %s", error)
        return 1

    stop = threading.Event()
    _install_stop_signals(stop)
    try:
        children = _launch_topology(ports)
    except Exception as error:  # noqa: BLE001 - fail closed; started children already stopped
        logger.error("governed topology failed to start: %s", error)
        return 1

    return supervise(children, stop)


if __name__ == "__main__":
    raise SystemExit(main())
