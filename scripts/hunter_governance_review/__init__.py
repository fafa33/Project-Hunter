"""Hunter Governance Review — mandatory merge gate for Project Hunter.

The gate combines three explicit layers:

1. Deterministic Governance Engine (``deterministic.py``)
2. LLM Architecture Audit (``llm_audit.py``)
3. Decision Engine (``decision.py``)

and then publishes the required GitHub commit status check named
``Hunter Governance Review``.

The gate is fail-closed. ``REVIEW_FAILED`` (missing secret, API failure,
timeout, malformed model output, unsupported response schema, stale review
pair, missing repository evidence, or an internal error) is never converted
into approval and is never skipped silently.

See ``docs/HUNTER_GOVERNANCE_REVIEW.md`` for the full specification.
"""

from __future__ import annotations

__version__ = "1.0.0"
