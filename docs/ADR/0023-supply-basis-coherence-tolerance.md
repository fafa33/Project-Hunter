# ADR 0023: Supply Basis Provider-Data Coherence Tolerance

## Status

Accepted. Amends ADR 0022 §"Scope: the first supported entity class," criterion 3 only. All other sections of ADR 0022 are reaffirmed, unchanged.

## Context

ADR 0022 Scope criterion 3 requires, as a precondition for canonical valuation eligibility: *"Circulating, total, and fully diluted supply are each independently observable and internally coherent under the existing `SupplyBasisSnapshot` contract (`circulating <= total <= fully_diluted`)."* As written, this is a hard, zero-tolerance inequality.

Issue #107 Milestone 1 (entity-agnostic valuation evidence acquisition, PR #110) attempted to register Sky (SKY) as the pilot entity under this criterion and hit a real, live-provider data condition: CoinGecko's `market_data` for Sky, in one API response snapshot, reported

```
circulating_supply = 23355983976.864086
total_supply       = 23462665147.36596
max_supply          = 23462665147.3
```

`total_supply` exceeds `max_supply` (the source of `fully_diluted_supply`) by a relative gap of `~2.8×10⁻¹²` — provider rounding, caching, or asynchronous field publication within one response, not a real claim that Sky has exceeded its own disclosed supply cap. `SupplyBasisSnapshot.__post_init__` (`src/hunter/value_capture/models.py`) originally hard-rejected any `total_supply > fully_diluted_supply` outright, refusing to persist the record at all. An interim workaround (PR #117) omitted `fully_diluted_supply` from the snapshot when incoherent; independent review correctly rejected that workaround, since an omitted component cannot satisfy criterion 3's "each independently observable" requirement.

The actual gap is not about Sky. Any entity sourced from a provider that exhibits this ordinary class of noise — and provider-side rounding/caching/async publication is not unique to CoinGecko or to Sky — would hit the identical hard rejection. Absent a general rule, each such entity would need its own ad hoc, PR-review-argued justification to be considered criterion-3-eligible: a project-specific exception repeated per entity rather than one settled rule applied uniformly, exactly the failure mode ADR 0020's anti-aliasing/anti-substitution stance and this repository's own established governance pattern (issue → specification → implementation → independent audit, used without exception for #88, #95, and every hardening PR since) exist to close off.

Before this ADR existed, the gap was closed at the code level only (PR #116/#117/#118, merged to `main`): `src/hunter/value_capture/models.py` now defines

```python
SUPPLY_COHERENCE_RELATIVE_TOLERANCE = Decimal("0.0001")
```

applied uniformly to whatever `quantity_components` are present on any `SupplyBasisSnapshot`, with no reference to Sky, CoinGecko, or any other named entity or provider anywhere in the check — entity-agnostic and provider-agnostic by construction, and independently confirmed not reverse-engineered to Sky's specific numbers (Sky's actual gap, `~2.8×10⁻¹²`, is roughly eight orders of magnitude below the tolerance). A `total_supply`/`fully_diluted_supply` gap within tolerance is persisted as ordinary accepted data, both raw values intact. A gap beyond tolerance is still never rejected or fabricated away: both raw values are preserved and `conflict_state` is elevated to `"open"` (forced unconditionally for an initial record, since it has no prior authorized correction that could legitimately have resolved anything) — which ADR 0022's existing Missingness section already treats as making the fair-value estimate unavailable until resolved by a proper correction. `circulating <= total` was left untouched by that fix: no real-world violation of that specific pair has been observed, and loosening it was neither requested nor evidenced.

That code change altered what "internally coherent" means for the purposes of ADR 0022 Scope criterion 3 before any accepted ADR authorized the change — relying only on an implementation report's own "architecture rationale" section for justification. This repository's ADR governance is explicit that a code change cannot supersede an ADR implicitly (`docs/ADR/README.md`: *"A code change, repository structure, persisted record, test, sprint document, or implementation report cannot supersede an ADR implicitly."*). This ADR closes that gap by formally, generically authorizing the rule that already shipped, rather than leaving it standing on implementation-report reasoning alone or granting Sky a one-off waiver.

## Decision

Amend ADR 0022 §"Scope: the first supported entity class," criterion 3. The criterion as accepted read:

> 3. Circulating, total, and fully diluted supply are each independently observable and internally coherent under the existing `SupplyBasisSnapshot` contract (`circulating <= total <= fully_diluted`).

It is amended, in full, to read:

> 3. Circulating, total, and fully diluted supply are each independently observable and internally coherent under the existing `SupplyBasisSnapshot` contract: `circulating <= total` is a hard, tolerance-free bound. `total <= fully_diluted` is evaluated against a fixed, versioned, entity-agnostic and provider-agnostic relative tolerance for provider precision/timing noise (`SUPPLY_COHERENCE_RELATIVE_TOLERANCE`, currently `0.0001`, as implemented in `hunter.value_capture.models`). A `total`/`fully_diluted` gap within tolerance is coherent, ordinary accepted data — no component may be omitted, rounded, or fabricated to force agreement. A gap beyond tolerance does not itself fail this criterion by silent rejection or silent acceptance: it surfaces as an open `SupplyBasisSnapshot` conflict via the existing `conflict_state` mechanism, which this ADR's Missingness section already treats as making the fair-value estimate unavailable until resolved by a proper correction. This tolerance is one fixed rule applied identically to every entity and every provider; it may not be adjusted, widened, waived, or bypassed for a specific entity, provider, or project except through a further accepted ADR amending this one by the same mechanism.

This amendment touches criterion 3 only. It does not touch Scope criteria 1, 2, or 4; the Permitted or Prohibited Methodology sections; the Terminology, Replay, Persistence, Provenance, Correction/Versioning, Confidence, Uncertainty, Comparability, Peer-Selection, Historical Validation, or Calibration sections (the Missingness section is referenced, not modified, since its existing text already covers the "beyond tolerance → open conflict → unavailable" chain without change). It authorizes no implementation, activates no Market Validation input, and changes no runtime behavior beyond formally ratifying the exact tolerance value and mechanism already shipped in `hunter.value_capture.models` prior to this ADR.

**Governance rule established by this ADR:** any future change to `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`'s value, or any extension of tolerance to a different component pair (e.g., `circulating <= total`), requires its own accepted ADR amendment under this same mechanism. It may never be changed by a code-only commit, and may never be scoped, conditioned, or waived for one named entity or provider.

**That future amendment may not simply edit the module constant.** `SupplyBasisSnapshot` persists no tolerance-policy identifier today, and every historical record is re-validated through `SupplyBasisSnapshot.__post_init__` against whatever value the constant holds at read time, not the value in effect when the record was originally accepted. Changing the constant with no further work would silently reinterpret every already-persisted `SupplyBasisSnapshot` on next read: a narrowed tolerance could flip a previously `conflict_state="none"` historical record to `"open"` on replay, and `strict_known_supply` would silently start excluding data that was valid and accepted at its original cutoff — a direct violation of this repository's strict-known replay-determinism guarantee (ADR 0020; ADR 0022's own Replay semantics section). Therefore, as a precondition of any future ADR that changes the tolerance's value or scope: that ADR must also add a persisted, versioned tolerance-policy identifier to `SupplyBasisSnapshot` and make coherence re-validation version-aware (evaluating each record against the policy version recorded on it, not the current module constant), before or as part of the same change. Until such an ADR exists, `SUPPLY_COHERENCE_RELATIVE_TOLERANCE` remains fixed at `0.0001`, unversioned, because this ADR changes nothing about it — it only formally authorizes the value already shipped, which no historical record was ever validated against a different value of.

## Compatibility

- ADR 0022 is reaffirmed in every respect other than criterion 3's exact wording above; no other ADR 0022 section, and no other accepted ADR, is superseded, weakened, or contradicted.
- `hunter.value_capture.models.SUPPLY_COHERENCE_RELATIVE_TOLERANCE` and its application inside `SupplyBasisSnapshot.__post_init__`, exactly as shipped in PR #116/#117/#118 (merged to `main` before this ADR), are retroactively and fully authorized by this ADR. No runtime, schema, or persisted-record change is made or required by this ADR itself — it is a documentation-only decision, consistent with ADR 0020's, ADR 0021's, and ADR 0022's own precedent for the same kind of consequence statement.
- Every `SupplyBasisSnapshot` accepted under the prior, strict, zero-tolerance check trivially satisfies the amended criterion (a tolerance of zero is a special case of a non-negative tolerance), so *this specific transition* — from a zero-tolerance check to `0.0001` — creates no replay or persisted-record compatibility issue: no record's `conflict_state` changes as a result of this ADR. This compatibility guarantee is specific to this one, monotonically-loosening transition; it is not a general property of the tolerance mechanism, and does not extend automatically to any future change in the tolerance's value (see the Decision section's precondition on future amendments, above).
- Issue #107 Milestone 1 / PR #110's registered Sky `SupplyBasisSnapshot` (`total_supply` fractionally above `fully_diluted_supply`, within tolerance, `conflict_state = "none"`) satisfies Scope criterion 3 as amended on the same general, entity-agnostic basis any future entity would rely on — not as an entity-specific exception, and this ADR grants Sky no authority or eligibility beyond what any other entity in the same provider-noise situation would also receive.

## Consequences

- Scope criterion 3 eligibility is now fully, generically, and auditably defined; no future entity needs a bespoke, PR-review-argued justification for ordinary provider precision/timing noise between `total_supply` and `fully_diluted_supply`.
- A future entity whose provider exhibits a *different* class of coherence noise (for example, between `circulating_supply` and `total_supply`) is not covered by this amendment and remains governed by the original hard, tolerance-free ADR 0022 text for that pair; closing that gap, if it is ever evidenced, requires its own future ADR amendment under this same mechanism, not a code-only fix.
- Implementation reports and pull requests that previously justified this behavior purely through implementation-report-level "architecture rationale" (see `docs/IMPLEMENTATION_REPORTS/v3.6.1-supply-basis-coherence-tolerance.md`) now have a proper ADR-level authorization to cite instead of, or alongside, that reasoning.
- `docs/ADR/README.md`'s index is updated in the same governed change to list this ADR.
- A structural replay-safety gap is identified, and gated against, but not itself fixed by this ADR: `SupplyBasisSnapshot` has no persisted tolerance-policy version, so re-validating a historical record always applies whatever value `SUPPLY_COHERENCE_RELATIVE_TOLERANCE` currently holds, not the value in effect when that record was accepted. This is not a live defect today, since the value has never changed and this ADR does not change it. It becomes a precondition on the *next* ADR that does change the value or its scope (see Decision), rather than unscoped schema work done now against no present need.

## Alternatives Considered

### Grant Sky a named, entity-specific waiver of Scope criterion 3

Rejected. An entity-scoped waiver creates an unauditable precedent: the next entity whose provider exhibits the same ordinary rounding/caching pattern would need its own waiver, argued from scratch in its own PR's review thread, with no settled rule to appeal to. This is the exact outcome this ADR exists to prevent, and it directly contradicts ADR 0022's own stated position that entity-class eligibility must be defined by criteria, never by naming a specific project.

### Leave Scope criterion 3 as a strict, zero-tolerance inequality and require providers to publish perfectly coherent data

Rejected. Already falsified by live production data (CoinGecko's real response for Sky). A zero-tolerance rule would make Scope criterion 3 permanently unsatisfiable for any entity sourced from a provider exhibiting ordinary rounding/caching/async-publication noise, blocking entity registration indefinitely for a non-economic, provider-formatting reason unrelated to the entity's actual eligibility.

### Let each implementation PR's code comments and implementation report continue to serve as the tolerance's sole architectural justification, without an ADR

Rejected, and identified as the actual governance gap this amendment closes. `SUPPLY_COHERENCE_RELATIVE_TOLERANCE` shipped to `main` (PR #116/#117/#118) and changed what "internally coherent" means under Scope criterion 3 before any accepted ADR authorized that change — an implicit, code-level redefinition of ADR-governed language, which `docs/ADR/README.md`'s own rule (*"A code change... cannot supersede an ADR implicitly"*) does not permit standing uncorrected.

### Omit `fully_diluted_supply` from the snapshot whenever it is smaller than `total_supply`, rather than defining a tolerance

Rejected — this was the actual interim approach (PR #117) and was itself found insufficient by independent review, because an omitted component cannot satisfy Scope criterion 3's "each independently observable" requirement. The tolerance-and-conflict-surfacing approach is the one that was implemented in PR #118 and is formally ratified by this ADR.

### Apply the same relative tolerance to `circulating <= total` as well, for symmetry

Rejected for now, as unevidenced scope creep: no real-world violation of `circulating <= total` has been observed, and this ADR authorizes only the specific rule already shipped and evidenced. Extending tolerance to that pair remains available to a future ADR amendment if and when a real provider-noise case is evidenced there, per the governance rule this ADR establishes.

### Add a persisted, versioned tolerance-policy identifier to `SupplyBasisSnapshot` now, even though the tolerance value is not changing

Rejected for this ADR specifically, though the underlying concern is real (independent review flagged it post-merge): without a persisted version, a *future* change to `SUPPLY_COHERENCE_RELATIVE_TOLERANCE` would re-validate every historical record against the new value on next read, silently altering already-accepted `conflict_state` outcomes and breaking strict-known replay determinism. Building that versioning machinery now, against a constant that has never changed and that this ADR does not change, would be unscoped schema/model work with no present trigger. Instead, this ADR's Decision section makes persisted versioning a mandatory precondition of the next ADR that actually changes the tolerance's value or scope — the risk is closed by gating the change that would cause it, not by speculative schema work today.
