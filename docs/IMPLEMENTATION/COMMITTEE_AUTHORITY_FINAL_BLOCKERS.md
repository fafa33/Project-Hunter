# Committee Authority Final Blockers

Implementation branch: `fix/committee-authority-final-blockers`

Governing sources:
- Issue #61
- `docs/AUDITS/POST_PR66_COMMITTEE_AUTHORITY_AUDIT.md`
- ADRs 0007, 0009, 0010, 0016, 0020, 0021

Required production changes:

1. Wire the authoritative committee composition root into the real production runtime path used by CLI, scheduler, and/or orchestrator. No caller-provided resolver or arbitrary `RepositoryFactory` may enter authoritative execution.
2. Bind committee input resolution to the approved runtime database/session construction. Repository origin must be verifiable independently of caller-supplied envelope fields.
3. Reject generic lifecycle states including `inactive`, `withdrawn`, `deprecated`, `disabled`, and equivalent non-current states even when `invalidated_at` is absent.
4. Remove generic snapshot metric authority for `risk` and `backtesting_reliability`. These metrics must either come from an explicitly approved typed owner/repository contract or remain fail-closed and abstain.
5. Add a deterministic end-to-end test covering authoritative SQL input persistence -> production composition/runtime execution -> committee assessment -> ranking -> champion persistence -> dashboard read path.
6. Preserve fail-closed behavior for opportunity, probability, pattern, necessity, alerts, valuation, comparative valuation, mispricing, and asymmetry until their canonical repository/domain reconstruction contracts exist.

Mandatory verification before merge:

```text
ruff check .
black --check .
mypy
pytest
```

No weakening of identity, cutoff, lineage, freshness, repository binding, lifecycle, unavailable-family, or dashboard read-only boundaries is permitted.
