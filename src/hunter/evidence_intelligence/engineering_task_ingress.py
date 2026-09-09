"""One canonical engineering-task ingress for governed Hunter execution.

Issue #436: "make the existing Smart Prompt Machine the mandatory bounded
ingress for Hunter engineering-agent tasks". Before this module, a governed
``PromptTaskRequest`` could be assembled by any library caller and driven
directly into ``SmartPromptMachine.compile_task``, and the trusted issuer edge
duplicated the composition root rather than sharing one named ingress.

This module introduces the single canonical engineering-task ingress. Every
governed engineering route passes through it: it accepts only caller task
intent (``PromptTaskRequest``) or the Issue authority that maps to one --
never a pre-built prompt string -- resolves the exact governed route and its
profile, enforces a machine-enforced hard input budget, and only then delegates
the bounded compile to the existing ``SmartPromptMachine``. Oversize caller
task text is either deterministically reduced within policy (the governed
``engineering.review-fix`` route whose bounded compiler is pinned to
``ENGINEERING_REVIEW_FIX_MAX_PROMPT_BYTES``) or fails closed before any
dispatch with a machine-readable ``PromptTaskOversizeError``. No provider or
model ever receives raw Issue/chat context from this path.

Raw-input size is never proof of dispatchability. After canonical
``SmartPromptMachine`` compilation the ingress inspects the compiled outcome
the machine already produced and only a build that is canonically ``READY``
with a concrete ``prompt_artifact_id`` may proceed toward an automation
envelope and executable handoff; ``INSUFFICIENT_BUDGET`` and every other
non-READY outcome fail closed with a deterministic machine-readable
``PromptTaskUnreadyError`` before any envelope is accepted or recorded. There
is no second renderer and no duplicate budget authority here -- the rendered
budget outcome is the machine's own.

Caller data selects nothing: the task key routes exactly through the governed
``PromptTaskRouteRegistry``, and provider, model, branch, reviewer, merge and
prompt-profile coordinates remain in the routing and operational configuration
this ingress is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter.evidence_intelligence.pre_model import EvidenceCapabilityConstraint
from hunter.evidence_intelligence.smart_prompt_machine import (
    PromptMachineProfile,
    PromptMachineProfileRegistry,
)
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_REVIEW_FIX_MAX_PROMPT_BYTES,
    ENGINEERING_REVIEW_FIX_PROFILE,
    ENGINEERING_REVIEW_FIX_ROUTE,
    PromptRouteConflict,
    PromptTaskAuthorityError,
    PromptTaskCompilationResult,
    PromptTaskRequest,
    PromptTaskRoute,
    PromptTaskRouteRegistry,
    SmartPromptMachine,
)

ENGINEERING_TASK_INGRESS_SCHEMA_VERSION = "engineering-task-ingress-v1"


@dataclass(frozen=True, slots=True)
class EngineeringTaskBudget:
    """Machine-enforced hard input budget for one governed engineering route."""

    task_key: str
    route_id: str
    profile_id: str
    maximum_input_bytes: int
    bounded_within_policy: bool


class PromptTaskOversizeError(PromptRouteConflict):
    """Raised when caller task text exceeds the route's governed input budget.

    ``reason`` carries the same coordinates as machine-readable key=value text
    while ``task_key``/``route_id``/``profile_id``/``maximum_input_bytes``/
    ``actual_bytes`` expose them structurally for the caller.
    """

    def __init__(
        self,
        *,
        task_key: str,
        route_id: str,
        profile_id: str,
        maximum_input_bytes: int,
        actual_bytes: int,
    ) -> None:
        if actual_bytes <= maximum_input_bytes:
            raise ValueError("oversize task text must exceed the route budget")
        self.task_key = task_key
        self.route_id = route_id
        self.profile_id = profile_id
        self.maximum_input_bytes = maximum_input_bytes
        self.actual_bytes = actual_bytes
        super().__init__(
            "ENGINEERING_TASK_OVERSIZE "
            f"task_key={task_key} route={route_id} profile={profile_id} "
            f"maximum={maximum_input_bytes} actual={actual_bytes}"
        )


class PromptTaskUnreadyError(PromptRouteConflict):
    """Raised when canonical compilation produced no READY dispatchable build.

    ``PromptTaskOversizeError`` fires on the raw caller-input size before
    compile; this fires on the canonical compiled outcome after the machine has
    rendered the actual prompt. Only a build whose allocation outcome is
    ``READY`` and which carries a concrete ``prompt_artifact_id`` may proceed to
    an automation envelope or executable handoff. ``reason`` carries the same
    coordinates as machine-readable key=value text while ``task_key``/``route_id``/
    ``profile_id``/``outcome``/``prompt_artifact_id``/``reason_codes``/
    ``preflight_size_bytes``/``available_input_bytes`` expose them structurally
    for the caller.
    """

    def __init__(
        self,
        *,
        task_key: str,
        route_id: str,
        profile_id: str,
        outcome: str,
        prompt_artifact_id: str | None,
        reason_codes: tuple[str, ...],
        preflight_size_bytes: int | None,
        available_input_bytes: int,
    ) -> None:
        self.task_key = task_key
        self.route_id = route_id
        self.profile_id = profile_id
        self.outcome = outcome
        self.prompt_artifact_id = prompt_artifact_id
        self.reason_codes = reason_codes
        self.preflight_size_bytes = preflight_size_bytes
        self.available_input_bytes = available_input_bytes
        super().__init__(
            "ENGINEERING_TASK_NOT_READY "
            f"task_key={task_key} route={route_id} profile={profile_id} "
            f"outcome={outcome} prompt_artifact_id={prompt_artifact_id} "
            f"reason_codes={','.join(reason_codes)} "
            f"preflight={preflight_size_bytes} available={available_input_bytes}"
        )


def _engineering_route_budget(route: PromptTaskRoute, profile: PromptMachineProfile) -> EngineeringTaskBudget:
    """Derive the machine-enforced budget for one governed engineering route."""
    if route.route_identity == ENGINEERING_REVIEW_FIX_ROUTE.route_identity:
        if profile.profile_identity != ENGINEERING_REVIEW_FIX_PROFILE.profile_identity:
            raise PromptTaskAuthorityError("engineering review-fix governed profile identity mismatch")
        return EngineeringTaskBudget(
            task_key=route.task_key,
            route_id=route.route_id,
            profile_id=profile.profile_id,
            maximum_input_bytes=ENGINEERING_REVIEW_FIX_MAX_PROMPT_BYTES,
            bounded_within_policy=True,
        )
    capability = profile.capability
    if not isinstance(capability, EvidenceCapabilityConstraint):
        raise PromptTaskAuthorityError("engineering route profile must declare an evidence capability constraint")
    return EngineeringTaskBudget(
        task_key=route.task_key,
        route_id=route.route_id,
        profile_id=profile.profile_id,
        maximum_input_bytes=capability.available_input_bytes,
        bounded_within_policy=False,
    )


class GovernedEngineeringTaskIngress:
    """The single canonical entry point for governed engineering tasks.

    ``compile`` is the only surface. It refuses anything that is not the
    canonical ``PromptTaskRequest``, resolves the exact governed route, enforces
    the route's hard input budget before any dispatch can occur, delegates the
    bounded compile to the existing ``SmartPromptMachine``, and then fails
    closed on any compiled outcome that is not canonically ``READY`` with a
    concrete prompt artifact -- so only a dispatchable build ever receives an
    automation envelope. Envelope lineage and Phase A/B/C identity stay exactly
    as the machine already issues them.
    """

    __slots__ = ("_machine", "_routes", "_profiles")

    def __init__(
        self,
        *,
        machine: SmartPromptMachine,
        routes: PromptTaskRouteRegistry,
        profiles: PromptMachineProfileRegistry,
    ) -> None:
        if not isinstance(machine, SmartPromptMachine):
            raise TypeError("the engineering-task ingress requires the canonical SmartPromptMachine")
        if not isinstance(routes, PromptTaskRouteRegistry):
            raise TypeError("the engineering-task ingress requires the canonical PromptTaskRouteRegistry")
        if not isinstance(profiles, PromptMachineProfileRegistry):
            raise TypeError("the engineering-task ingress requires the canonical PromptMachineProfileRegistry")
        if routes.profile_registry_identity != profiles.registry_identity:
            raise PromptTaskAuthorityError("engineering-task route/profile registry identity mismatch")
        self._machine = machine
        self._routes = routes
        self._profiles = profiles

    @property
    def route_registry_identity(self) -> str:
        """Return the exact governed route-registry identity this ingress routes through."""
        return self._routes.registry_identity

    @property
    def profile_registry_identity(self) -> str:
        """Return the exact Phase A profile-registry identity this ingress targets."""
        return self._profiles.registry_identity

    def budget_for(self, task_key: str) -> EngineeringTaskBudget:
        """Return the machine-enforced budget for one exact governed task key."""
        route = self._routes.resolve(task_key)
        profile = self._profiles.resolve(route.profile_id, route.profile_version)
        return _engineering_route_budget(route, profile)

    def compile(self, request: PromptTaskRequest) -> PromptTaskCompilationResult:
        """Compile exactly one governed caller task through the canonical path."""
        if not isinstance(request, PromptTaskRequest):
            raise TypeError("the engineering-task ingress accepts only the canonical PromptTaskRequest")
        route = self._routes.resolve(request.task_key)
        profile = self._profiles.resolve(route.profile_id, route.profile_version)
        budget = self.budget_for(request.task_key)
        if not budget.bounded_within_policy:
            actual = len(request.task_text.encode("utf-8"))
            if actual > budget.maximum_input_bytes:
                raise PromptTaskOversizeError(
                    task_key=request.task_key,
                    route_id=budget.route_id,
                    profile_id=budget.profile_id,
                    maximum_input_bytes=budget.maximum_input_bytes,
                    actual_bytes=actual,
                )
        compiled = self._machine.compile_task(request)
        # Raw-input size is not proof of dispatchability: the machine renders
        # JSON/spec/context over the caller task text, so only the canonical
        # compiled outcome -- reusing the pre-model authority the machine just
        # produced -- decides whether this build may proceed. Any non-READY
        # outcome (INSUFFICIENT_BUDGET, REPLAN_REQUIRED, ... ) or a missing
        # prompt artifact fails closed before an automation envelope exists.
        allocation = compiled.compilation.orchestration.build_result.allocation
        manifest = compiled.compilation.manifest
        if allocation.outcome != "READY" or manifest.prompt_artifact_id is None:
            raise PromptTaskUnreadyError(
                task_key=request.task_key,
                route_id=budget.route_id,
                profile_id=budget.profile_id,
                outcome=str(allocation.outcome),
                prompt_artifact_id=manifest.prompt_artifact_id,
                reason_codes=compiled.compilation.orchestration.build_result.build_record.reason_codes,
                preflight_size_bytes=allocation.preflight_size_bytes,
                available_input_bytes=allocation.available_input_bytes,
            )
        envelope = compiled.envelope
        if envelope.route_identity != route.route_identity:
            raise PromptTaskAuthorityError("compiled envelope route does not match the governed engineering route")
        if envelope.profile_identity != profile.profile_identity:
            raise PromptTaskAuthorityError("compiled envelope profile does not match the governed route profile")
        return compiled


__all__ = [
    "ENGINEERING_TASK_INGRESS_SCHEMA_VERSION",
    "EngineeringTaskBudget",
    "GovernedEngineeringTaskIngress",
    "PromptTaskOversizeError",
    "PromptTaskUnreadyError",
]
