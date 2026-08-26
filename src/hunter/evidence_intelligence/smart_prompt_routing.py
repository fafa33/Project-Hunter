from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime

from hunter.evidence_intelligence.pre_model_persistence import EvidencePreModelReconstruction
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_machine import (
    PromptBuildRequest,
    PromptCompilationResult,
    PromptContextCompiler,
    PromptMachineProfileRegistry,
    SmartPromptMachineError,
    SourceHandlingAuthorityResolver,
)
from hunter.execution import Clock

PROMPT_TASK_REQUEST_SCHEMA_VERSION = "smart-prompt-task-request-v1"
PROMPT_TASK_ROUTE_SCHEMA_VERSION = "smart-prompt-task-route-v1"
PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION = "smart-prompt-automation-envelope-v1"


class PromptRouteConflict(SmartPromptMachineError):
    """Raised when governed task routing is missing, duplicated, or conflicting."""


class PromptTaskAuthorityError(SmartPromptMachineError):
    """Raised when Phase B routing cannot prove the Phase A authority lineage."""


def _required_text(name: str, value: object) -> str:
    """Return one required text coordinate or fail closed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _task_key(value: object) -> str:
    """Validate one exact task-route key with no wildcard semantics."""
    task_key = _required_text("task_key", value)
    if "*" in task_key or "?" in task_key:
        raise PromptRouteConflict("task route keys must be exact; wildcards are forbidden")
    return task_key


def _identity(kind: str, value: object) -> str:
    """Produce a stable SHA-256 identity over canonical JSON."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"{kind}:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class PromptTaskRequest:
    """Caller-owned task data with no governed prompt-profile authority surface."""

    document_id: str
    execution_owner_id: str
    task_key: str
    task_text: str
    schema_version: str = PROMPT_TASK_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate the narrow caller surface and exact schema version."""
        _required_text("document_id", self.document_id)
        _required_text("execution_owner_id", self.execution_owner_id)
        _task_key(self.task_key)
        _required_text("task_text", self.task_text)
        if self.schema_version != PROMPT_TASK_REQUEST_SCHEMA_VERSION:
            raise PromptTaskAuthorityError("unknown Smart Prompt Machine task-request schema version")

    @property
    def request_id(self) -> str:
        """Return the stable identity of this caller task request."""
        return _identity("smart-prompt-task-request", asdict(self))


@dataclass(frozen=True)
class PromptTaskRoute:
    """Governed exact route from one task key to one Phase A prompt profile."""

    route_id: str
    version: str
    task_key: str
    profile_id: str
    profile_version: str
    schema_version: str = PROMPT_TASK_ROUTE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate exact route coordinates before registry admission."""
        _required_text("route_id", self.route_id)
        _required_text("version", self.version)
        _task_key(self.task_key)
        _required_text("profile_id", self.profile_id)
        _required_text("profile_version", self.profile_version)
        if self.schema_version != PROMPT_TASK_ROUTE_SCHEMA_VERSION:
            raise PromptRouteConflict("unknown Smart Prompt Machine route schema version")

    @property
    def route_identity(self) -> str:
        """Return the stable identity of this governed route."""
        return _identity("smart-prompt-task-route", asdict(self))


class PromptTaskRouteRegistry:
    """Immutable exact-route registry bound to one Phase A profile registry."""

    def __init__(
        self,
        routes: Iterable[PromptTaskRoute],
        *,
        profiles: PromptMachineProfileRegistry,
    ) -> None:
        """Validate route targets and reject duplicate or ambiguous route coordinates."""
        if not isinstance(profiles, PromptMachineProfileRegistry):
            raise TypeError("routes require the canonical PromptMachineProfileRegistry")
        entries: dict[str, PromptTaskRoute] = {}
        route_coordinates: dict[tuple[str, str], PromptTaskRoute] = {}
        for route in routes:
            if not isinstance(route, PromptTaskRoute):
                raise TypeError("route registry accepts PromptTaskRoute entries only")
            coordinate = (route.route_id, route.version)
            if coordinate in route_coordinates:
                existing = route_coordinates[coordinate]
                if existing.route_identity != route.route_identity:
                    raise PromptRouteConflict("conflicting governed route identity/version payload")
                raise PromptRouteConflict("duplicate governed route identity/version")
            if route.task_key in entries:
                existing = entries[route.task_key]
                if existing.route_identity != route.route_identity:
                    raise PromptRouteConflict("conflicting governed route for exact task key")
                raise PromptRouteConflict("duplicate governed route for exact task key")
            profiles.resolve(route.profile_id, route.profile_version)
            route_coordinates[coordinate] = route
            entries[route.task_key] = route
        self._routes = dict(sorted(entries.items()))
        self._profile_registry_identity = profiles.registry_identity
        self._registry_identity = _identity(
            "smart-prompt-task-route-registry",
            {
                "profile_registry_identity": self._profile_registry_identity,
                "routes": [
                    {
                        "task_key": route.task_key,
                        "route_identity": route.route_identity,
                    }
                    for route in self._routes.values()
                ],
            },
        )

    @property
    def registry_identity(self) -> str:
        """Return the insertion-order-independent route-registry identity."""
        return self._registry_identity

    @property
    def profile_registry_identity(self) -> str:
        """Return the exact Phase A profile-registry identity this router targets."""
        return self._profile_registry_identity

    def resolve(self, task_key: str) -> PromptTaskRoute:
        """Resolve one exact task key or fail closed without fallback matching."""
        key = _task_key(task_key)
        try:
            return self._routes[key]
        except KeyError as error:
            raise PromptRouteConflict("unknown governed Smart Prompt Machine task route") from error


@dataclass(frozen=True)
class PromptAutomationEnvelope:
    """Non-content identity envelope suitable for later automation transport."""

    task_request_id: str
    route_registry_identity: str
    profile_registry_identity: str
    route_identity: str
    profile_identity: str
    build_manifest_id: str
    build_record_id: str
    schema_version: str = PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate non-content lineage coordinates and envelope schema."""
        for name in (
            "task_request_id",
            "route_registry_identity",
            "profile_registry_identity",
            "route_identity",
            "profile_identity",
            "build_manifest_id",
            "build_record_id",
        ):
            _required_text(name, getattr(self, name))
        if self.schema_version != PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION:
            raise PromptTaskAuthorityError("unknown Smart Prompt Machine automation-envelope schema version")

    @property
    def envelope_id(self) -> str:
        """Return the stable identity of this non-content automation envelope."""
        return _identity("smart-prompt-automation-envelope", asdict(self))


@dataclass(frozen=True)
class PromptTaskCompilationResult:
    """Phase B result joining the Phase A compilation to its automation envelope."""

    envelope: PromptAutomationEnvelope
    compilation: PromptCompilationResult


class SmartPromptMachine:
    """Route caller task data through one governed Phase A prompt profile."""

    def __init__(
        self,
        *,
        repository: EvidenceIntelligenceRepository,
        profiles: PromptMachineProfileRegistry,
        routes: PromptTaskRouteRegistry,
        source_handling_resolver: SourceHandlingAuthorityResolver,
        clock: Clock | None = None,
    ) -> None:
        """Bind routing and compilation to one exact Phase A profile registry."""
        if not isinstance(profiles, PromptMachineProfileRegistry):
            raise TypeError("SmartPromptMachine requires the canonical profile registry")
        if not isinstance(routes, PromptTaskRouteRegistry):
            raise TypeError("SmartPromptMachine requires the canonical route registry")
        if routes.profile_registry_identity != profiles.registry_identity:
            raise PromptTaskAuthorityError("route/profile registry identity mismatch")
        self._profiles = profiles
        self._routes = routes
        self._compiler = PromptContextCompiler(
            repository=repository,
            profiles=profiles,
            source_handling_resolver=source_handling_resolver,
            clock=clock,
        )

    def compile_task(self, request: PromptTaskRequest) -> PromptTaskCompilationResult:
        """Resolve the governed route and delegate compilation to the Phase A compiler."""
        if not isinstance(request, PromptTaskRequest):
            raise TypeError("compile_task requires the canonical PromptTaskRequest")
        route = self._routes.resolve(request.task_key)
        profile = self._profiles.resolve(route.profile_id, route.profile_version)
        build_request = PromptBuildRequest(
            document_id=request.document_id,
            execution_owner_id=request.execution_owner_id,
            profile_id=route.profile_id,
            profile_version=route.profile_version,
            task_text=request.task_text,
        )
        compilation = self._compiler.compile(build_request)
        manifest = compilation.manifest
        if manifest.registry_identity != self._profiles.registry_identity:
            raise PromptTaskAuthorityError("compiled manifest profile-registry identity mismatch")
        if manifest.profile_identity != profile.profile_identity:
            raise PromptTaskAuthorityError("compiled manifest profile identity mismatch")
        envelope = PromptAutomationEnvelope(
            task_request_id=request.request_id,
            route_registry_identity=self._routes.registry_identity,
            profile_registry_identity=self._profiles.registry_identity,
            route_identity=route.route_identity,
            profile_identity=profile.profile_identity,
            build_manifest_id=manifest.manifest_id,
            build_record_id=manifest.build_record_id,
        )
        return PromptTaskCompilationResult(envelope=envelope, compilation=compilation)

    def strict_known_reconstruction(
        self,
        build_record_id: str,
        cutoff: datetime,
    ) -> EvidencePreModelReconstruction:
        """Delegate strict-known reconstruction to the existing Phase A repository path."""
        return self._compiler.strict_known_reconstruction(build_record_id, cutoff)
