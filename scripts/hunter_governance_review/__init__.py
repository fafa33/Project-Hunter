"""Hunter Governance Review — mandatory merge gate for Project Hunter.

The gate combines three explicit layers:

1. Authoritative Repository Context Resolver (``context.py``)
2. Deterministic Governance Engine (``deterministic.py``)
3. Decision Engine (``decision.py``)

and then publishes the required GitHub commit status check named
``Hunter Governance Review``.

The gate is entirely deterministic, repository-native, and CI-native: it
never calls an external LLM or any other network service besides GitHub
itself, requires no API key or provider secret, and makes zero paid or
external API calls. The gate is fail-closed. ``REVIEW_FAILED`` (missing
repository evidence, stale review pair, or an internal error) is never
converted into approval and is never skipped silently.

See ``docs/HUNTER_GOVERNANCE_REVIEW.md`` for the full specification.
"""

from __future__ import annotations

__version__ = "1.0.0"
