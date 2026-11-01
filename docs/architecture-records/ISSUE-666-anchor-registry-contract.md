# Issue #666 — shadow membership anchor and Registry historical proof contract

Status: implementation partial; security-blocked; no activation authorized. ADR 0040 remains authoritative.

## Protected anchor publication

- Dedicated ref: `refs/heads/hunter-state/v1/membership-anchor`; never the knowledge ledger.
- One flat file per Git commit: `membership-anchor.json`, containing a canonical Ed25519 signed claim under `ANCHOR_DOMAIN` with `repository`, strictly increasing `sequence`, and `digest`.
- An independently authorized signer supplies the signed claim; the publisher only validates the pinned public key and publishes via the existing `GitLedgerStore.append_files` lease-CAS. It must not create keys, fabricate claims, or create the protected ref.
- Before each read/write, require authenticated proof that pinned ruleset 24526712 is active, unchanged, and applies both deletion and non-fast-forward protections to this exact ref. Missing or stale branch-specific proof fails closed.
- The publisher requires an existing protected ref, exact expected HEAD, valid previous signed anchor, strictly advancing sequence, and changed digest. CAS conflict is a terminal refusal until the caller re-reads and reauthorizes. Readers select the *latest* commit, never the genesis commit.
- Initial ref creation and first signed anchor require a separately approved trusted bootstrap with independently verifiable provenance. No automatic bootstrap is permitted in PR #671. The ref was absent at the time of investigation; live bootstrap is not claimed.
- Historical SQLite must never serve as its own anchor. Signed claim validation alone does not establish ref protection or authoritative signing credentials.

## Historical canonical economic entity mapping

- `CandidateIdentity(outcome='exact')`, candidate IDs, ticker, `hunter_project` identifiers, `related_candidate_ids`, and current Registry upserts do **not** prove a canonical economic entity or its historical state (ADR 0040).
- A future Registry-owned immutable record must bind: candidate ID, authoritative canonical economic entity ID, representation/chain/contract, identity resolution and lifecycle outcome, Registry evidence IDs and source digests, observed/known/ingested timestamps with trusted provenance, previous record digest, and Registry policy/version. Its authenticated write authority and historical cutoff semantics must be independently verified.
- At cutoff T, only records authenticated as known/ingested no later than T qualify; supersession and conflict must be replayed without current-state inference. Any missing mapping, non-exact identity, duplicate economic entity, or conflicting chain representation fails closed.
- Until that Registry contract is implemented and verified, `RegistryIdentityVerifier.verify_entity` intentionally returns false even for a current-state `exact` identity. This disables candidate admission rather than granting unsupported proof. No baseline production behavior is changed.
- No parallel identity authority or independent ranking service is authorized; the Registry owns identity and the existing Market Validation Membership Authority owns membership.

Outstanding external gates: independently provisioned signing trust, protected anchor bootstrap and live GitHub verification, Registry-owned historical mapping/provenance, focused integration proof, and review resolution. No merge or production activation implied.
