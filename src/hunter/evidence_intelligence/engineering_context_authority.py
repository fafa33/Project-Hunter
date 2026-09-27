"""Repository-owned engineering context authority for implementation tasks.

The authority turns canonical DPM registry knowledge into deterministic,
bounded context before Smart Prompt Machine compilation. It never consumes
Issue/task prose when deciding which defect families apply.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hunter.task_scope import TaskScopeContract, path_matches_scope_entry

ENGINEERING_IMPLEMENT_TASK_KEY = "engineering.implement"

DEFAULT_DEFECT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "docs" / "DEFECT_REGISTRY.json"
ENGINEERING_IMPLEMENT_ROUTE_SURFACES = (
    ".github/",
    ".githooks/",
    ".hunter/",
    "build_backend/",
    "docs/",
    "pyproject.toml",
    "railway.toml",
    "scripts/",
    "src/",
    "tests/",
)
ENGINEERING_CONTEXT_SCHEMA_VERSION = "engineering-context-authority-v2"

#: A fresh agent session inherits repository state, not repository amnesia: it
#: must not restart onboarding-style discovery just because the session
#: restarted. This is a fixed, non-derived rule set -- it does not depend on
#: task text, scope, or the defect registry, so it never affects the
#: deterministic-repeat-compile invariant the callers of `compile()` rely on.
#: Reconciles the general "inspect the repository/branch/PR/architecture at
#: session start" guidance with the no-repository-rediscovery execution rule:
#: both hold, because this scopes what "inspect" means to what this task
#: actually needs, not a general audit.
EXECUTION_DISCIPLINE_RULES: tuple[str, ...] = (
    "A fresh agent session continues from current repository state; it is not "
    "a reason to restart repository onboarding or general architecture discovery.",
    "Read a file only for a task-specific reason; do not scan unrelated "
    "directories, and do not reread historical ADRs, Issues, or PRs merely for "
    "general familiarity.",
    "Do not run the full test suite for reassurance; verify this task with " "focused, scoped checks.",
    "Expanding scope beyond scope_allowed_paths above requires concrete "
    "evidence that the current scope is insufficient, not a preference for "
    "more context; state the evidence, the exact additional scope, and its "
    "expected cost before acting on it, never after.",
    "Acceptance criteria and mission scope are immutable to the agent: an "
    "agent may not silently omit, defer, reinterpret, or narrow a criterion "
    "it was given. A completion report that does so is not authoritative -- "
    "only Hunter's own current-state controllers are.",
    "An agent's own claim of completion, a final report, a passed local "
    "preflight, a pushed commit, or an opened pull request does not itself "
    "complete a mission. Completion is decided only by Hunter's independent, "
    "current-state evaluation of the exact HEAD (for example Hunter Merge "
    "Readiness's evaluate()/evaluate_completion_claim(), and the governing "
    "Issue's acceptance-criteria coverage check in hunter_pre_ready_review.py) "
    "-- never by what the agent asserts about its own work.",
)

CANONICAL_DEFECT_LIFECYCLES = frozenset(
    {"recorded", "regression-tested", "locally-enforced", "hosted-enforced", "merge-enforced", "prevented"}
)
CANONICAL_PREVENTION_BOUNDARIES = frozenset({"review", "local-pre-push", "hosted-gate", "merge-gate"})
#: Rank used only to break ties when bounded selection must prefer one
#: representative over another (dedup, and drop/downgrade ordering); it never
#: decides applicability, which stays a pure structural path match.
_LIFECYCLE_RANK = {
    stage: index
    for index, stage in enumerate(
        ("recorded", "regression-tested", "locally-enforced", "hosted-enforced", "merge-enforced", "prevented")
    )
}

#: Bound on the serialized ``applicable_defect_families`` payload when no
#: caller-supplied budget is given (direct/test use of this module). A Smart
#: Prompt Machine caller should instead pass its own derived share of the
#: governed profile's ``maximum_input_bytes`` -- see
#: ``smart_prompt_routing.ENGINEERING_IMPLEMENT_PREVENTION_CONTEXT_MAX_BYTES``
#: -- so this default is never the number that actually governs a real
#: engineering.implement compilation.
DEFAULT_PREVENTION_CONTEXT_MAX_BYTES = 12_000


class EngineeringContextAuthorityError(RuntimeError):
    """Raised when canonical engineering context cannot be proven safely."""


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineeringContextAuthorityError(f"{name} must be non-empty text")
    return value.strip()


def _path_intersects(left: str, right: str) -> bool:
    left = left.rstrip("/")
    right = right.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _is_file_leaf(path: str) -> bool:
    """Whether a changed_paths entry names an exact file rather than a directory.

    This is the only trusted, structural signal this module has for how
    precisely a family's applicability actually pins down the task at hand:
    a bare root like ``scripts/`` matches every file ever added under it,
    while ``scripts/hunter_review_orchestrator.py`` cannot mean anything
    broader than that one file. Neither caller-supplied task prose nor any
    external heuristic is consulted -- see the module docstring.

    Uses the registry's own trailing-slash convention (every directory root
    in ``docs/DEFECT_REGISTRY.json`` is written with a trailing ``/``, every
    file entry without one), rather than inspecting the leaf name for a dot:
    a dot-based test misclassifies dotfile directory roots like ``.github/``
    or ``.githooks/`` as exact files (their leaf name itself starts with a
    literal ``.``), and misclassifies an extensionless real file (a
    ``Dockerfile`` or ``Makefile``) as a directory.
    """

    return not path.endswith("/")


def _specificity(matched_paths: list[str]) -> tuple[bool, int, int]:
    """The best (is_file, depth, length) reading among a family's own matches.

    Used only to break ties in bounded selection -- never to decide whether a
    family is applicable, which stays the unchanged structural path-intersect
    test above.
    """

    best = (False, 0, 0)
    for path in matched_paths:
        candidate = (_is_file_leaf(path), path.rstrip("/").count("/") + 1, len(path))
        if candidate > best:
            best = candidate
    return best


def _lifecycle_rank(lifecycle: str) -> int:
    return _LIFECYCLE_RANK.get(lifecycle, -1)


class EngineeringContextAuthority:
    """Compile machine-owned prevention context for a governed engineering route."""

    def __init__(self, *, registry_path: Path = DEFAULT_DEFECT_REGISTRY_PATH) -> None:
        self._registry_path = Path(registry_path)

    def _families(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EngineeringContextAuthorityError("defect registry is unavailable or malformed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("families"), list):
            raise EngineeringContextAuthorityError("defect registry families must be a list")
        families: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in payload["families"]:
            if not isinstance(raw, dict):
                raise EngineeringContextAuthorityError("defect family must be an object")
            identifier = _required_text("defect family id", raw.get("id"))
            if identifier in seen:
                raise EngineeringContextAuthorityError(f"duplicate defect family: {identifier}")
            seen.add(identifier)
            applicability = raw.get("applicability")
            if not isinstance(applicability, dict):
                raise EngineeringContextAuthorityError(f"{identifier} applicability must be an object")
            changed_paths = applicability.get("changed_paths")
            if not isinstance(changed_paths, list) or not changed_paths:
                raise EngineeringContextAuthorityError(f"{identifier} changed_paths must be a non-empty list")
            if any(not isinstance(path, str) or not path.strip() for path in changed_paths):
                raise EngineeringContextAuthorityError(f"{identifier} changed_paths must contain non-empty text")
            prevention = raw.get("prevention")
            if not isinstance(prevention, dict):
                raise EngineeringContextAuthorityError(f"{identifier} prevention must be an object")
            boundary = _required_text(f"{identifier} prevention boundary", prevention.get("boundary"))
            if boundary not in CANONICAL_PREVENTION_BOUNDARIES:
                raise EngineeringContextAuthorityError(
                    f"{identifier} prevention boundary must be canonical: {boundary!r}"
                )
            _required_text(f"{identifier} title", raw.get("title"))
            _required_text(f"{identifier} invariant", raw.get("invariant"))
            lifecycle = _required_text(f"{identifier} lifecycle", raw.get("lifecycle"))
            if lifecycle not in CANONICAL_DEFECT_LIFECYCLES:
                raise EngineeringContextAuthorityError(f"{identifier} lifecycle must be canonical: {lifecycle!r}")
            equivalence_class = prevention.get("equivalence_class")
            if equivalence_class is not None and (
                not isinstance(equivalence_class, str) or not equivalence_class.strip()
            ):
                raise EngineeringContextAuthorityError(
                    f"{identifier} prevention equivalence_class must be a non-empty string when declared"
                )
            families.append(raw)
        return families

    def compile(
        self,
        task_key: str,
        *,
        scope: TaskScopeContract,
        budget_bytes: int = DEFAULT_PREVENTION_CONTEXT_MAX_BYTES,
    ) -> dict[str, object]:
        if task_key != ENGINEERING_IMPLEMENT_TASK_KEY:
            raise EngineeringContextAuthorityError(f"unsupported engineering context route: {task_key}")
        if not isinstance(scope, TaskScopeContract):
            raise EngineeringContextAuthorityError(
                "engineering implementation requires the canonical task scope contract"
            )
        incomplete = scope.incompleteness()
        if incomplete:
            raise EngineeringContextAuthorityError(incomplete)
        if len(scope.base_sha) != 40 or any(c not in "0123456789abcdef" for c in scope.base_sha):
            raise EngineeringContextAuthorityError("scope contract base_sha must be an exact lowercase commit SHA")
        if isinstance(budget_bytes, bool) or not isinstance(budget_bytes, int) or budget_bytes <= 0:
            raise EngineeringContextAuthorityError("prevention context budget_bytes must be a positive integer")

        candidates: list[dict[str, Any]] = []
        for family in self._families():
            paths = family["applicability"]["changed_paths"]

            def permitted(path: str) -> bool:
                intersections = [entry for entry in scope.allowed_paths if _path_intersects(path, entry)]
                if not intersections:
                    return False
                # A prohibition removes an applicability surface only when it
                # covers that whole allowed intersection. A nested prohibition
                # leaves the rest of a broader allowed surface applicable.
                return any(
                    not any(path_matches_scope_entry(candidate, prohibited) for prohibited in scope.prohibited_paths)
                    for candidate in intersections
                )

            matched = [path for path in paths if permitted(path)]
            if not matched:
                continue
            prevention = family["prevention"]
            full_item: dict[str, object] = {
                "id": family["id"],
                "title": family["title"],
                "invariant": family["invariant"],
                "lifecycle": family["lifecycle"],
                "prevention_boundary": prevention["boundary"],
            }
            guard_reference = prevention.get("guard_reference")
            if guard_reference is not None:
                full_item["guard_reference"] = _required_text(f"{family['id']} guard_reference", guard_reference)
            compact_item: dict[str, object] = {
                "id": family["id"],
                "title": family["title"],
                "prevention_boundary": prevention["boundary"],
            }
            candidates.append(
                {
                    "id": family["id"],
                    "specificity": _specificity(matched),
                    "lifecycle_rank": _lifecycle_rank(family["lifecycle"]),
                    "equivalence_class": prevention.get("equivalence_class"),
                    "full": full_item,
                    "compact": compact_item,
                }
            )
        if not candidates:
            raise EngineeringContextAuthorityError("no applicable defect families for engineering implementation route")

        # Deduplication: within a group of families the registry explicitly
        # declares equivalent (same non-empty prevention.equivalence_class),
        # only the single best representative is ever rendered. This never
        # affects applicability -- every group member is still "applicable"
        # canonical knowledge -- it only stops the compiled prompt from
        # inlining the same prevention requirement's prose more than once.
        # The winner is chosen by the same priority bounded selection uses
        # below (most specific match, then most-advanced lifecycle, then id
        # as a final fully-deterministic tiebreak), never by registry order.
        def priority(entry: dict[str, Any]) -> tuple[Any, ...]:
            return (entry["specificity"], entry["lifecycle_rank"], entry["id"])

        groups: dict[str, list[dict[str, Any]]] = {}
        singletons: list[dict[str, Any]] = []
        for entry in candidates:
            group_key = entry["equivalence_class"]
            if group_key is None:
                singletons.append(entry)
            else:
                groups.setdefault(group_key, []).append(entry)
        rendered_entries = list(singletons)
        for members in groups.values():
            rendered_entries.append(max(members, key=priority))
        rendered_entries.sort(key=lambda entry: entry["id"])

        # Bounded rendering: try full detail for every applicable entry first,
        # regardless of whether its match was through an exact file or a
        # directory root, and only degrade -- in deterministic,
        # lowest-priority-first steps -- once that genuinely does not fit.
        # A directory-matched family is never downgraded merely because it
        # matched through a directory; it is downgraded only when the budget
        # actually requires it, exactly like a file-matched family is.
        # Canonical knowledge is never deleted by this: every step below is a
        # rendering decision over the same `rendered_entries`, and a dropped
        # entry's id is still recorded so nothing disappears silently.
        def render(entry: dict[str, Any], full: bool) -> dict[str, object]:
            return entry["full"] if full else entry["compact"]

        def blob_bytes(items: list[dict[str, object]]) -> int:
            ordered = sorted(items, key=lambda item: str(item["id"]))
            return len(json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

        included = list(rendered_entries)
        elided: list[str] = []
        full_ids = {entry["id"] for entry in included}

        def rendered_now() -> list[dict[str, object]]:
            return [render(entry, entry["id"] in full_ids) for entry in included]

        rendered = rendered_now()
        if blob_bytes(rendered) > budget_bytes:
            # Tier 2: downgrade directory-matched (non-file-specific) entries
            # to compact, broadest/shortest/lowest-id first, until it fits or
            # none of them remain full.
            downgradable = sorted(
                (entry for entry in included if not entry["specificity"][0]),
                key=priority,
            )
            for entry in downgradable:
                if blob_bytes(rendered) <= budget_bytes:
                    break
                full_ids.discard(entry["id"])
                rendered = rendered_now()
        if blob_bytes(rendered) > budget_bytes:
            # Tier 3: drop directory-matched entries entirely, broadest first,
            # until it fits or none remain.
            droppable = sorted(
                (entry for entry in included if not entry["specificity"][0]),
                key=priority,
            )
            for entry in droppable:
                if blob_bytes(rendered) <= budget_bytes:
                    break
                included.remove(entry)
                full_ids.discard(entry["id"])
                elided.append(str(entry["id"]))
                rendered = rendered_now()
        if blob_bytes(rendered) > budget_bytes:
            # Tier 4: only file-specific entries remain full; downgrade them
            # to compact form, least-specific first, before failing closed.
            downgrade_order = sorted((entry for entry in included if entry["id"] in full_ids), key=priority)
            for entry in downgrade_order:
                if blob_bytes(rendered) <= budget_bytes:
                    break
                full_ids.discard(entry["id"])
                rendered = rendered_now()
        final_bytes = blob_bytes(rendered)
        if final_bytes > budget_bytes:
            overflow = final_bytes - budget_bytes
            largest = sorted(included, key=priority, reverse=True)[:3]
            raise EngineeringContextAuthorityError(
                "governed prevention context for "
                f"{task_key} exceeds its {budget_bytes}-byte budget by {overflow} bytes even after full "
                "compaction; largest remaining contributors: " + ", ".join(str(entry["id"]) for entry in largest)
            )
        result: dict[str, object] = {
            "schema_version": ENGINEERING_CONTEXT_SCHEMA_VERSION,
            "task_key": task_key,
            "scope_task_id": scope.task_id,
            "scope_base_sha": scope.base_sha,
            "scope_allowed_paths": list(scope.allowed_paths),
            "scope_prohibited_paths": list(scope.prohibited_paths),
            "applicable_defect_families": rendered,
            "execution_discipline": list(EXECUTION_DISCIPLINE_RULES),
        }
        if elided:
            # Canonical knowledge is never deleted -- these ids are still
            # applicable and still fully described in docs/DEFECT_REGISTRY.json
            # -- only omitted from this bounded prompt because higher-priority
            # matches (a more specific path, or a more fully-enforced lifecycle)
            # already claimed the budget. Recorded so the omission is never
            # silent, matching the fail-closed contract used when even this
            # is not enough to fit (see the EngineeringContextAuthorityError
            # raised above).
            result["elided_family_ids"] = sorted(elided)
        return result

    def canonical_json(
        self, task_key: str, *, scope: TaskScopeContract, budget_bytes: int = DEFAULT_PREVENTION_CONTEXT_MAX_BYTES
    ) -> str:
        return json.dumps(
            self.compile(task_key, scope=scope, budget_bytes=budget_bytes),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
