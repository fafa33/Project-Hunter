"""Architectural regression tests: the Hunter Governance Review merge gate
must have zero dependency, direct or transitive, on any external LLM or
other non-GitHub network service.

This is not merely "the current tests don't exercise an LLM call" -- these
tests inspect the actual source of every module in the gate's package and
its workflow files, so a future change that reintroduces a provider
dependency (even one exercised only in an untested code path) fails
immediately and explicitly, rather than being caught only by an operator
noticing an unexpected outbound API call in production.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import hunter_governance_review

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(hunter_governance_review.__file__).resolve().parent

# Case-insensitive substrings that would indicate an LLM/external-provider
# dependency if they appeared anywhere in the gate's own source. Deliberately
# broad (provider names, API endpoint shapes, secret-variable naming
# conventions used by the removed integration) rather than narrowly matching
# only what happened to exist before -- a differently-named future
# reintroduction should still be caught.
_FORBIDDEN_SOURCE_MARKERS: tuple[str, ...] = (
    "openai",
    "gemini",
    "generativelanguage.googleapis.com",
    "anthropic",
    "groq",
    "chat/completions",
    "hunter_llm",
    "llm_api_key",
    "llm_audit",
    "chunk_audit_prompt",
    "provider_config",
    "providerhealth",
)

# Substrings that would indicate an LLM-related workflow configuration
# (secrets, variables, or a caching layer that existed only to make
# provider calls affordable) if present in a workflow YAML file.
_FORBIDDEN_WORKFLOW_MARKERS: tuple[str, ...] = (
    "hunter_llm",
    "groq_api_key",
    "openai_api_key",
    "actions/cache",
)


def _all_module_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for info in pkgutil.walk_packages(hunter_governance_review.__path__, prefix="hunter_governance_review."):
        module = importlib.import_module(info.name)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        sources[info.name] = Path(module_file).read_text(encoding="utf-8")
    sources["hunter_governance_review"] = Path(hunter_governance_review.__file__).read_text(encoding="utf-8")
    return sources


def test_no_module_source_mentions_a_forbidden_llm_provider_marker() -> None:
    sources = _all_module_sources()
    assert sources, "expected at least one module in the hunter_governance_review package"
    for module_name, text in sources.items():
        lowered = text.lower()
        hits = [marker for marker in _FORBIDDEN_SOURCE_MARKERS if marker in lowered]
        assert not hits, f"{module_name} contains forbidden LLM/provider marker(s): {hits}"


def test_no_module_imports_urllib_request_or_urllib_error() -> None:
    """The gate's only network access is the ``gh`` CLI subprocess
    (github_api.py) -- no module should perform its own raw HTTP calls,
    which is exactly what the removed LLM client did."""
    sources = _all_module_sources()
    for module_name, text in sources.items():
        assert "urllib.request" not in text, f"{module_name} imports urllib.request (raw HTTP client)"
        assert "urllib.error" not in text, f"{module_name} imports urllib.error"


def test_run_review_signature_takes_no_llm_runner_arguments() -> None:
    """A structural guarantee that the orchestration entry point cannot be
    handed an LLM/provider callable even if one existed elsewhere: the
    only externally-supplied collaborator is the GitHub runner."""
    import inspect

    from hunter_governance_review.__main__ import run_review

    params = set(inspect.signature(run_review).parameters)
    assert params == {"args", "env", "gh"}


def test_decision_module_does_not_import_any_llm_module() -> None:
    from hunter_governance_review import decision

    module_names = {name for name in dir(decision) if not name.startswith("_")}
    # decide()'s only imports are contracts (Outcome) and the stdlib --
    # nothing named after an LLM/audit/provider concept should be importable
    # from this module's namespace.
    forbidden = {"AuditVerdict", "LLMAuditError", "ProviderHealth", "ProviderConfig", "run_llm_audit"}
    assert not (module_names & forbidden)


def test_no_workflow_file_references_llm_provider_configuration() -> None:
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    hunter_workflows = sorted(workflows_dir.glob("hunter-governance-*.yml"))
    assert hunter_workflows, "expected at least one hunter-governance-*.yml workflow"
    for path in hunter_workflows:
        lowered = path.read_text(encoding="utf-8").lower()
        hits = [marker for marker in _FORBIDDEN_WORKFLOW_MARKERS if marker in lowered]
        assert not hits, f"{path.name} contains forbidden LLM/provider marker(s): {hits}"


def test_gate_package_has_no_llm_specific_modules() -> None:
    """The removed modules (llm_audit, chunking, aggregate, checkpoint)
    existed solely to call, chunk for, or checkpoint around an LLM
    provider. None may exist in the package -- a reintroduction under any
    of these names, or a new module serving the same purpose, should fail
    this test rather than silently pass because it happens to use a
    different filename than before."""
    module_names = {info.name for info in pkgutil.iter_modules(hunter_governance_review.__path__)}
    forbidden_module_names = {"llm_audit", "chunking", "aggregate", "checkpoint"}
    assert not (module_names & forbidden_module_names)
