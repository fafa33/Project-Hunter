"""Bounded repository-owned evidence reconstruction for historical findings.

A third-party review finding is a *candidate*, not a verdict. This module
reconstructs what the repository itself did about it, using only bounded,
locally-available evidence:

- whether the reviewed PR ever shipped, and the commit that landed it;
- whether the reviewed file and the symbols the claim names still exist;
- whether the path was subsequently rewritten (superseded) or narrowly corrected;
- whether a focused regression test or deterministic guard was added afterwards;
- repository-owner dispositions (authored comments, thread replies, owner
  review bodies, owner PR comments) that actually address the claim;
- whether the claim is about external availability rather than Hunter code;
- whether an existing canonical family's invariant matches exactly.

Two separations are load-bearing and are enforced here rather than left to
interpretation:

``historical defect truth`` vs ``recurring invariant``
    A finding being corrected after review proves the defect was real *at that
    commit*. It does not by itself justify a new canonical family. Confirmed
    truth and family clustering are reported separately.

``evidence`` vs ``reviewer authority``
    Reviewer identity and self-declared severity never select a disposition.
    Only repository-owned artifacts and local history do.

Nothing here writes the canonical registry: new families are returned as
proposals so that family authoring can be sequenced after the namespace
canonicalization phase.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence import historical_evidence_adjudication as base
from hunter.evidence_intelligence import semantic_invariant_extraction

# ---------------------------------------------------------------------------
# Is this text a claim about the code at all?
# ---------------------------------------------------------------------------
#
# EXCLUDED_NOT_A_CLAIM is permitted only when the item contains no substantive
# technical defect/behavior/invariant claim whatsoever -- UI chrome, container
# text, metadata, status, acknowledgement, or wrapper. Lacking a file/line anchor
# is explicitly NOT sufficient: substantive findings are routinely stated at
# review level, several of them naming their own root cause, and owner review
# bodies that assert a blocker must still reach the owner-disposition rule.
#
# The test is positive, not a length threshold, and it fails toward "substantive".
# Judging by length called "Fixed in a535c1c the guard now rejects the request"
# chrome purely because it was short. Recognising only the specific chrome text
# observed in the ledger means anything unrecognised is treated as a claim, where
# absent real evidence it lands in OWNER_REQUIRED and stays visible. Calling
# chrome a claim costs review effort; calling a claim chrome deletes it from
# history.

_CHROME_SEGMENT_PATTERNS = (
    r"included review availability:.*$",
    r"your plan provides up to \d+ included reviews? per hour;?\s*\d*.*$",
    r"actionable comments posted:\s*\d+",
    r"\[!?CAUTION\].*$",
    r"\[!NOTE\] quiet mode is enabled.*$",
    r"copilot (?:was )?unable to review (?:this pull request|any files in this pull request).*$",
    r"copilot wasn'?t able to review any files in this pull request\.?$",
    r"copilot reviewed \d+ out of \d+ changed files in this pull request and " r"generated no new comments\.?",
    r"^pull request overview\s*",
    r"copilot review overview.*$",
    r"one or more issues must be addressed before approval.*$",
    r"get a fresh assessment.*$",
    r"here are some automated review suggestions for this pull request\.?\s*",
    r"reviewed commit:?\s*`?[0-9a-f]{7,40}`?",
    r"^\s*@[\w-]+\s+review\s*$",
    r"(?:\U0001f4a1\s*)?codex review\b",
    r"^changes requested\s*$",
    r"^reviewed \d+ files? in this pull request.*$",
    r"^\U0001f7e1\s*changes recommended\s*$",
    r"^\U0001f535\s*needs a closer look\s*$",
    r"^\U0001f7e0\s*commented\s*$",
)
_CHROME_SEGMENT = re.compile("|".join(_CHROME_SEGMENT_PATTERNS), re.IGNORECASE)

# Unbalanced blockquote/closing-tag residue left behind by markup stripping.
_TAG_RESIDUE = re.compile(r"</?[A-Za-z][\w-]*")

# The complete set of residual texts accepted as chrome on this ledger. Anything
# else is a claim, by construction rather than by judgement.
_CHROME_RESIDUAL = re.compile(
    r"^(?:"
    r"here are some automated review suggestions for this pull request\.?"
    r"|generated no new comments\.?"
    r")$",
    re.IGNORECASE,
)

# An assertion that some technical condition is violated. Deliberately broad:
# a false positive costs review effort, a false negative loses a finding.
_SUBSTANCE_MARKER = re.compile(
    r"\b(?:does not|doesn'?t|do not|don'?t|fails? to|failed to|is not|isn'?t|are not|aren'?t"
    r"|missing|ignores?|ignored|admits?|violat\w*|forgeable|unauthenticated|unsigned|unverified"
    r"|toctou|race condition|deadlock|double[- ]?count|duplicat\w*|root cause|blocker"
    r"|p[0-3]\b|changes required|must not|must be|should not|should be|instead of"
    r"|without (?:validating|checking|verifying|confirming)|silently|leaks?|corrupt\w*"
    r"|stale|expired|bypass\w*|regress\w*|inconsistenc\w*|forgeab\w*|overwrit\w*|truncat\w*)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Derived constituent claims (grouped / summary review bodies)
# ---------------------------------------------------------------------------
#
# Not every reviewer posts every finding as its own review comment. CodeRabbit in
# quiet mode posts only the most important comments inline and groups the rest
# under "Other comments"; its "Outside diff range" findings are grouped the same
# way; and Copilot's suppressed-comment lists carry full finding bodies.
# ``clean_comment_text`` removes ``<details>`` blocks wholesale, so on those
# bodies the finding text is deleted *before* any claim test runs. What is left is
# the vendor's own chrome ("Actionable comments posted: 5"), the container is
# correctly found to assert nothing on its own, and the finding it carried is
# deleted from history along with it.
#
# On this ledger that silently lost real claims. All 391 "not a claim" exclusions
# were justified by exactly that residue, while 19 of them -- across 17 pull
# requests -- verifiably carry anchored claim bodies. Those claims exist nowhere
# else in their pull request, because quiet mode means they were never posted
# inline.
#
# The correction is bounded and deterministic: split a container body into its
# anchored claim blocks, and adjudicate each block as its own derived claim with
# a stable identity and exact parent provenance. The frozen ledger is never
# written; derived claims are a second, additive view of an observation that
# already exists, counted separately from the ledger items so the
# exact-reconciliation claim keeps its meaning.
#
# Deliberately NOT expanded: index-only and truncated overview bodies. A Copilot
# ``ccr-overview-v2`` "New Resolved since last review" list carries truncated
# finding titles and anchors to a discussion thread -- no rule, no evidence, no
# location. Neither grammar below can match it, and the prose test rejects any
# entry that turns out to be a bare link, so expanding one would manufacture
# claims out of a navigation index.

DERIVED_CLAIM_ID_PREFIX = "hunter-derived-claim-v1"

# CodeRabbit claim block: ``<summary>path[-start-end] (N)</summary>`` followed by
# the finding. The ``(N)`` count and a real file extension are both required, so
# the surrounding group headers ("Nitpick comments (3)", "Proposed fix",
# "Prompt for AI Agents") cannot be mistaken for findings. The prefix before the
# path accepts leading symbols but never a dot, so ``.github/workflows/x.yml``
# keeps its dot instead of silently becoming a different path.
_CLAIM_BLOCK = re.compile(
    r"<summary>[^.\w\s]*\s*(?P<path>[\w@/.+-]*\.[A-Za-z0-9]{1,8})"
    r"(?:[-:#](?P<start>\d{1,7})(?:\s*-\s*(?P<end>\d{1,7}))?)?"
    r"\s*\(\d+\)\s*</summary>(?P<body>.*?)(?=<summary>|\Z)",
    re.DOTALL,
)

# CodeRabbit prefixes a finding with its own review metadata -- the diff range, the
# category, the severity and the effort label. That is reviewer decoration, not the
# claim, so it is dropped; when the block's own summary carried no range, this line
# is where the range actually is.
_CODE_RABBIT_META = re.compile(
    r"^(?:[\s>]|</?[a-zA-Z][^>]*>)*"
    r"(?:`(?P<start>\d{1,7})(?:\s*-\s*(?P<end>\d{1,7}))?`|(?P<bare>\d{1,7})(?:\s*-\s*\d{1,7})?)"
    r"\s*:[^\n]*\n?",
    re.MULTILINE,
)

# Copilot suppressed-comment entry: a bolded ``**path:line**`` on its own line.
# The bold is required: a bare ``path:line`` in prose is not a claim anchor, and
# that precision is what keeps a navigation list from becoming a finding.
_CLAIM_ENTRY = re.compile(
    r"(?m)^[ \t>]*(?P<bold>\*\*)(?P<path>[\w@/.+-]*\.[A-Za-z0-9]{1,8}):(?P<line>\d{1,7})\1[ \t]*$"
)

# An entry that is only a thread anchor and/or quoted code is navigation, not a
# claim. Removing links and fences first is what makes that decidable.
_LINK_OR_FENCE = re.compile(r"\[[^\]]*\]\([^)]*\)|```.*?```", re.DOTALL)
_WORD = re.compile(r"[A-Za-z]{4,}")

# A floor for skipping headers, one-word residue, and truncated titles. It is a
# noise filter, not a judgement about a claim's merit: everything that clears it
# is adjudicated on its own evidence.
_MIN_DERIVED_CLAIM_CHARS = 30

# Recorded on the parent so a reader can see the body was inspected, not skipped.
_INDEX_ONLY_MARKER = re.compile(
    r"ccr-overview-v2|copilot review overview|new (?:un)?resolved comments? since last review",
    re.IGNORECASE,
)


def derived_claim_id(
    parent_observation_id: str,
    block_index: int,
    path: str,
    line: int | None,
    line_end: int | None,
) -> str:
    """Stable identity for one derived claim.

    Derived only from the parent's ledger id, the block's ordinal, and its anchor
    -- never from message text, run order, or a timestamp -- so the same parent
    observation always yields the same child ids, and any derived claim can be
    joined back to its parent and its exact block by arithmetic alone.
    """
    return "derived-" + base.stable_json_digest(
        {
            "version": DERIVED_CLAIM_ID_PREFIX,
            "parent_observation_id": parent_observation_id,
            "block_index": block_index,
            "path": path,
            "line": line,
            "line_end": line_end,
        }
    )


def _derived_block_is_claim(text: str) -> tuple[bool, str]:
    """``(is_claim, reason)`` for one extracted block body."""
    cleaned = base.clean_comment_text(text)
    prose = _LINK_OR_FENCE.sub(" ", cleaned)
    if not _WORD.search(prose):
        return False, "anchor, thread link, and quoted code only: no claim prose"
    if len(cleaned) < _MIN_DERIVED_CLAIM_CHARS:
        return False, f"only {len(cleaned)} chars remain after markup removal"
    return is_substantive_claim(cleaned)


def _derived_block_text(raw: str) -> str:
    cleaned = base.clean_comment_text(raw)
    return re.sub(r"\s+", " ", _TAG_RESIDUE.sub(" ", cleaned)).strip()


def derived_claims(observation: dict[str, Any], observation_id: str) -> tuple[list[dict[str, Any]], str]:
    """Constituent claims carried by a grouped/summary review body.

    Returns ``(claims, reason)``. ``claims`` is empty -- with the reason recorded
    -- for a body that carries none, including the index-only overview bodies
    this must never expand.

    Claims are taken from the first grammar that matches, so a body carrying both
    formats cannot yield the same finding twice.
    """
    message = observation.get("message") or ""
    parent_id = observation_id or str(observation.get("observation_id") or "")
    blocks: list[tuple[str, str, int | None, int | None, str]] = []

    for match in _CLAIM_BLOCK.finditer(message):
        path = match.group("path")
        line = int(match.group("start")) if match.group("start") else None
        line_end = int(match.group("end")) if match.group("end") else None
        body = match.group("body") or ""
        meta = _CODE_RABBIT_META.match(body)
        if meta is not None:
            recovered = meta.group("start") or meta.group("bare")
            recovered_end = meta.group("end")
            if line is None and recovered:
                line = int(recovered)
                line_end = int(recovered_end or recovered)
            body = body[meta.end() :]
        blocks.append(("coderabbit-claim-block", path, line, line_end, body))
    if not blocks:
        anchors = list(_CLAIM_ENTRY.finditer(message))
        for position, match in enumerate(anchors):
            stop = anchors[position + 1].start() if position + 1 < len(anchors) else len(message)
            blocks.append(
                (
                    "copilot-suppressed-entry",
                    match.group("path"),
                    int(match.group("line")),
                    None,
                    message[match.end() : stop],
                )
            )

    if not blocks:
        if _INDEX_ONLY_MARKER.search(message):
            return [], "index-only overview body: titles and thread anchors, no anchored claim block"
        return [], "no anchored claim block in the body"

    claims: list[dict[str, Any]] = []
    refused: list[str] = []
    for index, (grammar, path, line, line_end, raw) in enumerate(blocks):
        text = _derived_block_text(raw)
        keep, reason = _derived_block_is_claim(text)
        if not keep:
            refused.append(f"block {index} ({path}): {reason}")
            continue
        claims.append(
            {
                "observation_id": derived_claim_id(parent_id, index, path, line, line_end),
                "parent_observation_id": parent_id,
                "block_index": index,
                "grammar": grammar,
                "path": path,
                "line": line,
                "line_end": line_end,
                "claim_text": text,
            }
        )
    reason = f"{len(claims)} anchored claim block(s) from {len(blocks)} anchored block(s)"
    if refused:
        reason += "; refused " + "; ".join(refused)
    return claims, reason


def is_substantive_claim(message: str | None) -> tuple[bool, str]:
    """``(is_substantive, reason)`` for one observation's text.

    Review chrome carries counts, quotas, plan limits, bot names, review
    requests, and "reviewed N of N files, no new comments". None of that asserts
    anything about the code, and none of it is a finding. Everything else is
    treated as a claim, including review-level findings that name no file and
    no line.
    """
    cleaned = base.clean_comment_text(message)
    if not cleaned:
        return False, "no text remains after removing markup and hidden tool transcripts"
    residual = _TAG_RESIDUE.sub(" ", _CHROME_SEGMENT.sub(" ", cleaned))
    residual = re.sub(r"\s+", " ", residual).strip(" -—|:")
    if not re.search(r"[A-Za-z0-9]", residual) or _CHROME_RESIDUAL.match(residual):
        return False, f"only review chrome remains ({residual[:60]!r})"
    if _SUBSTANCE_MARKER.search(residual):
        return True, "residual text asserts a technical condition"
    return True, f"{len(residual)} chars of unrecognised content remain, so it is not provably chrome"


# ---------------------------------------------------------------------------
# Dispositions
# ---------------------------------------------------------------------------

CONFIRMED = "CONFIRMED"
EXISTING_FAMILY = "EXISTING_FAMILY"
EXCLUDED_FALSE_POSITIVE = "EXCLUDED_FALSE_POSITIVE"
EXCLUDED_STYLE = "EXCLUDED_STYLE"
EXCLUDED_OBSOLETE_OR_SUPERSEDED = "EXCLUDED_OBSOLETE_OR_SUPERSEDED"
INFRASTRUCTURE_OR_PROVIDER = "INFRASTRUCTURE_OR_PROVIDER"
OWNER_REQUIRED = "OWNER_REQUIRED"
RULE_RESOLVED_NON_RECURRING = "R11-resolved-non-recurring-no-shared-invariant"
_SHA_PREFIX = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
# Beyond the owner-supplied disposition list: a review umbrella comment, summary,
# or instruction names no file and no line, so it is not a claim about this
# repository's code at all. Reported as its own reconciled category rather than
# forced into a defect verdict it does not support.
EXCLUDED_NOT_A_CLAIM = "EXCLUDED_NOT_A_CLAIM"
# A grouped/summary review body that carries no claim of its own: it is vendor
# chrome plus N anchored claim blocks, each of which is carried forward as its own
# derived claim. It is reported separately from ``EXCLUDED_NOT_A_CLAIM`` because
# calling it "not a claim" was what deleted real findings from history with it.
# The container itself asserts nothing, and its derived claims are counted
# separately, so this category never hides a claim.
EXCLUDED_SUMMARY_CONTAINER = "EXCLUDED_SUMMARY_CONTAINER"
# A confirmed defect that was genuinely corrected, whose violated invariant is
# documented and whose prevention (regression test plus guard) is verified to
# exist on the integration base, and for which no shared violated invariant can
# be demonstrated against any other finding. There is nothing left to prevent,
# so it is not unmapped history: it is a resolved one-off. It is deliberately
# NOT a family, and it is only reached when every one of those facts holds.
RESOLVED_NON_RECURRING = "RESOLVED_NON_RECURRING"
# A confirmed finding whose violated invariant was verified and then judged, on
# the evidence, to be the same invariant as another finding's. It is mapped to an
# evidence-backed canonical family: an existing registry family when the verdict
# names one, otherwise a new-family proposal carrying the invariant, root cause,
# execution boundary, applicability and prevention for that family. Keyword
# clustering cannot produce this; only a recorded verdict can.
PROPOSED_NEW_FAMILY = "PROPOSED_NEW_FAMILY"
RULE_PROPOSED_NEW_FAMILY = "R12-mapped-to-evidence-backed-family"

TERMINAL_DISPOSITIONS = frozenset(
    {
        CONFIRMED,
        RESOLVED_NON_RECURRING,
        PROPOSED_NEW_FAMILY,
        EXISTING_FAMILY,
        EXCLUDED_FALSE_POSITIVE,
        EXCLUDED_STYLE,
        EXCLUDED_OBSOLETE_OR_SUPERSEDED,
        INFRASTRUCTURE_OR_PROVIDER,
        EXCLUDED_NOT_A_CLAIM,
        EXCLUDED_SUMMARY_CONTAINER,
    }
)

RECONSTRUCTION_SCHEMA_VERSION = "hunter-historical-evidence-reconstruction-v2"

# Rule identifiers, recorded on every decision for auditability.
RULE_OWNER_DISPOSITION = "E1-owner-disposition"
RULE_CANONICAL_INVARIANT_MATCH = "E2-canonical-invariant-match"
RULE_CORRECTED_WITH_TRACEABLE_FIX = "E3-corrected-with-traceable-fix"
RULE_OWNER_REJECTION = "E4-owner-rejection"
RULE_NON_BEHAVIORAL = "E5-non-behavioral"
RULE_EXTERNAL_EVENT = "E6-external-provider-event"
RULE_IMPLEMENTATION_ABANDONED = "E7-implementation-abandoned"
RULE_IMPLEMENTATION_SUPERSEDED = "E8-implementation-superseded"
RULE_INSUFFICIENT_EVIDENCE = "E9-insufficient-evidence"
# A container body whose claims were extracted instead of discarded.
RULE_SUMMARY_CONTAINER = "R13-summary-container-claims-expanded"

# ---------------------------------------------------------------------------
# Claim lexicon (structural, not stylistic)
# ---------------------------------------------------------------------------

# Identifiers a claim names. Backticked, snake_case, or CamelCase.
_IDENTIFIER = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`"
    r"|\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b"
    r"|\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b"
)

# Words that are structure, not symbols: they name a concept, not code.
_GENERIC_IDENTIFIERS = frozenset(
    {
        "github",
        "python",
        "json",
        "yaml",
        "pytest",
        "readme",
        "workflow",
        "click",
        "async",
        "await",
    }
)

# External availability events: not Hunter implementation defects.
_EXTERNAL_EVENT = re.compile(
    r"\b(rate[ -]?limit|quota|429|500|502|503|504|timed? ?out|timeout"
    r"|transient(?:ly)? (?:unavailable|failed)|outage|service unavailable"
    r"|secondary rate|abuse detection|network error|connection reset)\b",
    re.I,
)

# Non-behavioral observations: naming, wording, formatting, ordering taste.
_NON_BEHAVIORAL = re.compile(
    r"\b(naming|typo|misspell(?:ed|ing)?|grammar|spelling|reword|rename(?:d)? (?:the |this )?"
    r"(?:variable|function|parameter|constant|field|method|class)"
    r"|renaming|formatting|whitespace|docstring wording|comment (?:wording|style)"
    r"|inconsistent naming|alphabetical|alphabetize|sort(?:ed)? (?:the )?imports?)\b",
    re.I,
)

# An owner statement that a claim is wrong.
_REJECTION = re.compile(
    r"\b(false positive|not an issue|not a bug|not a defect|incorrect finding"
    r"|invalid finding|won't fix|wontfix|by design|intended behaviour|intended behavior"
    r"|as designed|not reproducible|cannot reproduce)\b",
    re.I,
)

# An owner statement that the claim was a real defect that got corrected.
_CORRECTION = re.compile(
    r"\b(fixed|resolved|corrected|remediated|addressed|patched|repaired|hardened"
    r"|closed|now requires|now rejects|now validates|now bounds|now enforces)\b",
    re.I,
)

_FIX_SHA = re.compile(r"\b([0-9a-f]{7,40})\b")

# A regression test or guard is evidence of an enforced invariant.
_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$", re.I)
_GUARD_PATH = re.compile(
    r"preflight|guard|invariant|enforcement|validator|validation|policy|schema|_check\b|conftest", re.I
)

# Rewrites that supersede rather than correct.
_REWRITE_DELETION_RATIO = 0.5
_MIN_SUPERSEDE_COMMITS = 2


# ---------------------------------------------------------------------------
# Bounded read-only local history
# ---------------------------------------------------------------------------


class GitHistory:
    """Read-only, cached view of the integration base.

    Only reads are issued. The reviewed PR head commits are not reachable in a
    squash-merged clone, so the integration base is the anchor for "does this
    implementation still exist".
    """

    def __init__(self, repo_root: Path, ref: str = "origin/main") -> None:
        self.repo_root = repo_root
        self.ref = ref

    def _run(self, args: Sequence[str]) -> str | None:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        return proc.stdout if proc.returncode == 0 else None

    @lru_cache(maxsize=1)  # noqa: B019 - one history per process
    def tree_paths(self) -> frozenset[str]:
        out = self._run(["ls-tree", "-r", "--name-only", self.ref])
        return frozenset(out.splitlines()) if out else frozenset()

    def path_exists(self, path: str) -> bool:
        return path in self.tree_paths()

    @lru_cache(maxsize=4096)  # noqa: B019 - bounded per path
    def content(self, path: str) -> str | None:
        return self._run(["show", f"{self.ref}:{path}"])

    @lru_cache(maxsize=4096)  # noqa: B019 - bounded per path
    def path_history(self, path: str) -> tuple[tuple[str, str, str], ...]:
        """``(sha, author_date, subject)`` for commits touching ``path``."""
        out = self._run(["log", "--format=%H%x1f%aI%x1f%s", "--follow", self.ref, "--", path])
        if not out:
            return ()
        entries: list[tuple[str, str, str]] = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                entries.append((parts[0], parts[1], parts[2]))
        return tuple(entries)

    def last_change(self, path: str) -> tuple[str, str, str] | None:
        history = self.path_history(path)
        return history[0] if history else None

    def commits_after(self, path: str, not_before: str | None) -> tuple[tuple[str, str, str], ...]:
        """Commits touching ``path`` strictly after ``not_before`` (ISO date).

        Temporal, not positional. Without a real timestamp this would silently
        return the path's entire history, and every "corrected after review"
        claim would rest on commits that predate the review.
        """
        if not not_before:
            return ()
        return tuple(entry for entry in self.path_history(path) if entry[1] > not_before)

    def test_symbol_index(self, symbols: Sequence[str]) -> dict[str, list[str]]:
        """Base test files that reference each symbol.

        This is the deterministic form of "a focused regression test was added
        for this behaviour": not a commit-message keyword, but an actual test in
        the integration base that exercises the symbol the claim named.
        """
        if not hasattr(self, "_symbol_index"):
            index: dict[str, set[str]] = {}
            for path in sorted(self.tree_paths()):
                if not _TEST_PATH.search(path):
                    continue
                content = self.content(path)
                if not content:
                    continue
                for symbol in extract_symbols(content):
                    index.setdefault(symbol, set()).add(path)
            self._symbol_index = {key: sorted(value) for key, value in index.items()}
        found: dict[str, list[str]] = {}
        for symbol in symbols:
            hits = self._symbol_index.get(symbol)
            if hits:
                found[symbol] = hits
        return found


# ---------------------------------------------------------------------------
# Evidence signals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reconstruction:
    """Bounded repository-owned evidence for a single candidate finding."""

    observation_id: str
    source_pr: int
    path: str | None
    line: int | None
    rule: str
    disposition: str
    evidence: str
    historical_defect_truth: str
    invariant: str
    symbols: tuple[str, ...] = ()
    path_exists_at_base: bool = False
    symbols_surviving: tuple[str, ...] = ()
    symbols_absent: tuple[str, ...] = ()
    pr_merged: bool = False
    pr_state: str = ""
    pr_anchor: str | None = None
    commits_after_review: int = 0
    regression_test_added: bool = False
    guard_added: bool = False
    fix_reference: str = ""
    owner_authority: str = ""
    claimed_family_id: str | None = None
    execution_boundary: str = ""
    prevention_mechanism: str = ""
    applicability_surface: tuple[str, ...] = ()
    evidence_signals: dict[str, Any] = field(default_factory=dict)
    # Set only on a derived claim: the exact parent observation, the block it came
    # from, and that block's anchor. Null on every ledger observation.
    derived_from: dict[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.disposition in TERMINAL_DISPOSITIONS

    def to_json(self) -> dict[str, Any]:
        payload = {
            "observation_id": self.observation_id,
            "source_pr": self.source_pr,
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "disposition": self.disposition,
            "evidence": self.evidence,
            "historical_defect_truth": self.historical_defect_truth,
            "invariant": self.invariant,
            "symbols": list(self.symbols),
            "path_exists_at_base": self.path_exists_at_base,
            "symbols_surviving": list(self.symbols_surviving),
            "symbols_absent": list(self.symbols_absent),
            "pr_merged": self.pr_merged,
            "pr_state": self.pr_state,
            "pr_anchor": self.pr_anchor,
            "commits_after_review": self.commits_after_review,
            "regression_test_added": self.regression_test_added,
            "guard_added": self.guard_added,
            "fix_reference": self.fix_reference,
            "owner_authority": self.owner_authority,
            "claimed_family_id": self.claimed_family_id,
            "execution_boundary": self.execution_boundary,
            "prevention_mechanism": self.prevention_mechanism,
            "applicability_surface": list(self.applicability_surface),
            "evidence_signals": self.evidence_signals,
            "derived_from": self.derived_from,
        }
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Reconstruction:
        return cls(
            observation_id=payload["observation_id"],
            source_pr=payload["source_pr"],
            path=payload.get("path"),
            line=payload.get("line"),
            rule=payload["rule"],
            disposition=payload["disposition"],
            evidence=payload["evidence"],
            historical_defect_truth=payload.get("historical_defect_truth", ""),
            invariant=payload.get("invariant", ""),
            symbols=tuple(payload.get("symbols") or ()),
            path_exists_at_base=bool(payload.get("path_exists_at_base")),
            symbols_surviving=tuple(payload.get("symbols_surviving") or ()),
            symbols_absent=tuple(payload.get("symbols_absent") or ()),
            pr_merged=bool(payload.get("pr_merged")),
            pr_state=payload.get("pr_state", ""),
            pr_anchor=payload.get("pr_anchor"),
            commits_after_review=int(payload.get("commits_after_review") or 0),
            regression_test_added=bool(payload.get("regression_test_added")),
            guard_added=bool(payload.get("guard_added")),
            fix_reference=payload.get("fix_reference", ""),
            owner_authority=payload.get("owner_authority", ""),
            claimed_family_id=payload.get("claimed_family_id"),
            execution_boundary=payload.get("execution_boundary", ""),
            prevention_mechanism=payload.get("prevention_mechanism", ""),
            applicability_surface=tuple(payload.get("applicability_surface") or ()),
            evidence_signals=payload.get("evidence_signals") or {},
            derived_from=payload.get("derived_from"),
        )


def extract_symbols(message: str | None) -> tuple[str, ...]:
    """Identifiers the claim names, in order, de-duplicated.

    Structure, not style: this is what lets a claim be located in the code.
    """
    found: list[str] = []
    for backticked, snake, camel in _IDENTIFIER.findall(message or ""):
        token = backticked or snake or camel
        if not token or len(token) <= 4:
            continue
        if token.lower() in _GENERIC_IDENTIFIERS:
            continue
        if token not in found:
            found.append(token)
    return tuple(found)


def execution_boundary_of(path: str | None) -> str:
    """The boundary a defect in ``path`` violates: its owning module."""
    if not path:
        return ""
    parts = path.split("/")
    if path.startswith("src/hunter/") and len(parts) > 2:
        return "/".join(parts[:3])
    if path.startswith("src/"):
        return "/".join(parts[:2])
    if path.startswith("docs/"):
        return "docs"
    if path.startswith(".github/workflows/"):
        return ".github/workflows"
    if path.startswith("tests/"):
        return "tests"
    return parts[0] if parts else ""


def requirement_clause(message: str | None) -> str:
    """The normative clause of a claim: the invariant it asserts.

    Used to detect duplicate families. This is the requirement the claim
    states, with reviewer chrome removed, not a similarity score.
    """
    text = base.clean_comment_text(message)
    text = re.sub(r"^\**\s*", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Repository-owner disposition index
# ---------------------------------------------------------------------------


def owner_dispositions(record: dict[str, Any], owner_login: str) -> list[dict[str, Any]]:
    """Every repository-owned statement that could dispose of a claim.

    Sources, all repository-owned: the owner's own inline comments, replies on
    the finding's own thread, the owner's review bodies, and the owner's
    PR-level comments. Third-party bodies are never indexed, because a
    third-party label is evidence, not authority.
    """
    evidence = record.get("evidence") or {}
    found: list[dict[str, Any]] = []
    for comment in evidence.get("review_comments") or []:
        if (comment.get("user") or {}).get("login") != owner_login:
            continue
        found.append(
            {
                "source": "inline" if comment.get("in_reply_to_id") is None else "thread_reply",
                "id": comment.get("id"),
                "in_reply_to_id": comment.get("in_reply_to_id"),
                "body": comment.get("body") or "",
                "path": comment.get("path"),
            }
        )
    for review in evidence.get("reviews") or []:
        if (review.get("user") or {}).get("login") != owner_login:
            continue
        if (review.get("body") or "").strip():
            found.append({"source": "review_body", "id": review.get("id"), "body": review.get("body") or ""})
    for comment in evidence.get("issue_comments") or []:
        if (comment.get("user") or {}).get("login") != owner_login:
            continue
        found.append({"source": "pr_comment", "id": comment.get("id"), "body": comment.get("body") or ""})
    return found


_EVENT_ID_SUFFIX = re.compile(r"(\d+)\s*$")


def numeric_event_id(event_id: str | None) -> str:
    """The bare comment id behind a ``review-comment-<id>`` event id.

    GitHub comment ids are integers but the scanner's ``event_id`` is a prefixed
    string. Comparing the two directly never matches, which would silently
    disable every thread-reply disposition.
    """
    match = _EVENT_ID_SUFFIX.search(str(event_id or ""))
    return match.group(1) if match else str(event_id or "")


def classify_owner_statement(body: str) -> tuple[str, str]:
    """``(kind, fix_reference)`` for one owner statement."""
    classification, _substance, fix_reference = base.split_owner_disposition(body)
    if classification == "confirmed":
        return "correction", fix_reference
    if classification in base.EXCLUSION_CLASSIFICATIONS:
        return "rejection", fix_reference
    text = base.clean_comment_text(body)
    if _REJECTION.search(text):
        return "rejection", fix_reference
    if _CORRECTION.search(text):
        return "correction", fix_reference
    return "none", fix_reference


def match_owner_disposition(
    observation: dict[str, Any],
    dispositions: Sequence[dict[str, Any]],
    symbols: Sequence[str],
    owner_login: str = "",
) -> dict[str, Any] | None:
    """The owner statement that addresses *this* claim, if one exists.

    Only per-finding evidence counts: the owner authoring the finding's own
    comment, or the owner replying on the finding's own thread. See the comment
    on the deliberately rejected weaker matchers below.
    """
    event_id = str(observation.get("event_id") or "")
    # The observation is itself a review comment. When the repository owner
    # authored it, that is first-party disposition evidence, and it is the
    # single largest source of it.
    if str(observation.get("reviewer") or "") == str(owner_login):
        kind, fix_reference = classify_owner_statement(str(observation.get("message") or ""))
        if kind != "none":
            return {
                "source": "owner_authored",
                "kind": kind,
                "fix_reference": fix_reference,
                "match": "self",
            }
    for entry in dispositions:
        if entry["source"] != "thread_reply":
            continue
        if str(entry.get("in_reply_to_id")) == numeric_event_id(event_id):
            kind, fix_reference = classify_owner_statement(str(entry.get("body") or ""))
            if kind != "none":
                return {**entry, "kind": kind, "fix_reference": fix_reference, "match": "thread_reply"}
    # Deliberately NOT matched, despite looking reasonable:
    #
    #   * owner inline comments elsewhere in the same file ("same_file")
    #   * owner review bodies and PR-level comments, matched by naming the
    #     claim's path or one of its symbols ("pr_level")
    #
    # None of these is a disposition of *this* finding. An owner statement that
    # merely contains the claim's symbol or file path establishes that the owner
    # wrote about the same area, not that they adjudicated the claim. On the real
    # ledger the path/symbol heuristic alone produced 143 confirmations across
    # 119 pull requests -- findings whose owner never mentioned them -- which
    # would have quietly closed a sixth of the ambiguous set on reviewer
    # authority alone.
    #
    # Only two forms are per-finding evidence: the owner authoring the finding's
    # own comment, and the owner replying on the finding's own thread. Anything
    # weaker must fall through to the bounded history rules or OWNER_REQUIRED.
    return None


# ---------------------------------------------------------------------------
# Bounded local-history evidence
# ---------------------------------------------------------------------------


def pr_anchor(record: dict[str, Any]) -> dict[str, Any]:
    """Temporal and cryptographic anchor for a pull request, from captured evidence.

    PR head commits are unreachable in a squash-merged clone, so the anchor is
    the pull request's own last commit: its SHA makes an owner-cited fix
    reference verifiable, and its date makes "changed after this review" a real
    temporal filter instead of the path's whole history.
    """
    commits = (record.get("evidence") or {}).get("commits") or []
    shas: list[str] = []
    dates: list[str] = []
    for entry in commits:
        sha = str(entry.get("sha") or "")
        date = str(((entry.get("commit") or {}).get("author") or {}).get("date") or "")
        if sha:
            shas.append(sha)
        if date:
            dates.append(date)
    if not dates:
        metadata = (record.get("evidence") or {}).get("metadata") or {}
        for key in ("merged_at", "updated_at", "created_at"):
            if metadata.get(key):
                dates.append(str(metadata[key]))
                break
    return {
        "anchor_sha": shas[-1] if shas else None,
        "anchor_date": max(dates) if dates else None,
        "commit_shas": shas,
    }


def collect_history_evidence(
    observation: dict[str, Any],
    symbols: Sequence[str],
    history: GitHistory,
    anchor_date: str | None,
) -> dict[str, Any]:
    """What the integration base says about the reviewed implementation."""
    path = observation.get("path")
    signals: dict[str, Any] = {
        "path_exists_at_base": False,
        "symbols_surviving": (),
        "symbols_absent": (),
        "commits_after_review": 0,
        "regression_test_symbols": (),
        "regression_test_added": False,
        "path_rewritten": False,
    }
    if not path:
        return signals
    signals["path_exists_at_base"] = history.path_exists(path)
    if not signals["path_exists_at_base"]:
        return signals
    content = history.content(path) or ""
    signals["symbols_surviving"] = tuple(s for s in symbols if s.split(".")[-1] in content)
    signals["symbols_absent"] = tuple(s for s in symbols if s.split(".")[-1] not in content)
    later = history.commits_after(path, anchor_date)
    signals["commits_after_review"] = len(later)
    signals["post_review_subjects"] = tuple(subject for _sha, _date, subject in later[:12])
    tested = history.test_symbol_index(symbols)
    signals["regression_test_symbols"] = tuple(sorted(tested))
    signals["regression_test_added"] = bool(tested)
    signals["path_rewritten"] = len(later) >= _MIN_SUPERSEDE_COMMITS
    return signals


# ---------------------------------------------------------------------------
# Deterministic disposition
# ---------------------------------------------------------------------------

NO_DEFECT_TRUTH = "none"
DEFECT_WAS_REAL_AND_CORRECTED = "real-and-corrected"
DEFECT_WAS_REAL_UNCORRECTED = "real-but-uncorrected"
CLAIM_WAS_INCORRECT = "claim-incorrect"
NOT_A_DEFECT = "not-a-defect"
NOT_APPLICABLE = "not-applicable"
UNDETERMINED = "undetermined"


def _prior_proven_state(observation: dict[str, Any], state: str) -> str | None:
    """Honour a terminal resolution the scan already proved.

    The scan preloaded these from canonical backfill evidence. Re-deriving them
    would discard a proven canonical mapping and replace it with a weaker
    re-inference.
    """
    classification = str(observation.get("classification") or "")
    if state not in base.TERMINAL_LEDGER_STATES or not classification:
        return None
    return classification


def reconstruct(
    observation: dict[str, Any],
    ledger_state: str,
    record: dict[str, Any],
    *,
    observation_id: str = "",
    owner_login: str,
    history: GitHistory,
    families: Sequence[dict[str, Any]],
    anchors: dict[int, str],
    prior: base.Adjudication | None = None,
) -> Reconstruction:
    """Resolve one candidate to a deterministic disposition.

    Precedence is evidence-ordered and reviewer-neutral. Reviewer identity and
    self-declared severity never appear in any branch below.
    """
    path = observation.get("path")
    message = observation.get("message") or ""
    symbols = extract_symbols(message)
    pr_number = int(observation.get("source_pr") or record.get("pr_number") or 0)
    anchor_info = pr_anchor(record)
    anchor_date = anchor_info["anchor_date"]
    anchor = anchor_info["anchor_sha"] or anchors.get(pr_number)
    pr_merged = bool(record.get("merged"))
    pr_state = str(record.get("state") or "")
    boundary = execution_boundary_of(path)
    invariant = requirement_clause(message)

    signals = collect_history_evidence(observation, symbols, history, anchor_date)
    dispositions = owner_dispositions(record, owner_login)
    owner_match = match_owner_disposition(observation, dispositions, symbols, owner_login)

    def build(
        rule: str,
        disposition: str,
        truth: str,
        evidence: str,
        *,
        claimed: str | None = None,
        fix_reference: str = "",
        owner_authority: str = "",
        extra: dict[str, Any] | None = None,
        invariant_text: str | None = None,
        claimed_symbols: tuple[str, ...] | None = None,
    ) -> Reconstruction:
        return Reconstruction(
            # The frozen ledger's canonical id, carried through verbatim, so every
            # reconstruction joins back to its ledger item by identity. Deriving a
            # fresh id from the event id instead would make the exact-reconciliation
            # claim a count agreement that no artifact can verify.
            observation_id=observation_id
            or str(observation.get("observation_id") or f"obs-{observation.get('event_id')}"),
            source_pr=pr_number,
            path=path,
            line=observation.get("line"),
            rule=rule,
            disposition=disposition,
            evidence=evidence,
            historical_defect_truth=truth,
            invariant=invariant if invariant_text is None else invariant_text,
            symbols=symbols if claimed_symbols is None else claimed_symbols,
            path_exists_at_base=bool(signals["path_exists_at_base"]),
            symbols_surviving=tuple(signals["symbols_surviving"]),
            symbols_absent=tuple(signals["symbols_absent"]),
            pr_merged=pr_merged,
            pr_state=pr_state,
            pr_anchor=anchor,
            commits_after_review=int(signals["commits_after_review"]),
            regression_test_added=bool(signals["regression_test_added"]),
            guard_added=bool(signals["regression_test_added"]),
            fix_reference=fix_reference,
            owner_authority=owner_authority,
            claimed_family_id=claimed,
            execution_boundary=boundary,
            applicability_surface=(path,) if path else (),
            evidence_signals={**{k: v for k, v in signals.items()}, **(extra or {})},
            derived_from=observation.get("derived_from"),
        )

    # -- E0: a terminal resolution the scan already proved --------------------
    proven = _prior_proven_state(observation, ledger_state)
    if proven:
        claimed = observation.get("claimed_family_id")
        if ledger_state == "existing-family" and claimed:
            return build(
                base.RULE_PRESERVE_TERMINAL,
                EXISTING_FAMILY,
                DEFECT_WAS_REAL_AND_CORRECTED,
                f"scan already resolved this observation to canonical family {claimed}",
                claimed=str(claimed),
            )
        if proven == "confirmed":
            return build(
                base.RULE_PRESERVE_TERMINAL,
                CONFIRMED,
                DEFECT_WAS_REAL_AND_CORRECTED,
                "scan already resolved this observation as a confirmed defect",
            )
        mapping = {
            "false-positive": EXCLUDED_FALSE_POSITIVE,
            "style": EXCLUDED_STYLE,
            "obsolete": EXCLUDED_OBSOLETE_OR_SUPERSEDED,
        }
        if proven in mapping:
            return build(
                base.RULE_PRESERVE_TERMINAL,
                mapping[proven],
                CLAIM_WAS_INCORRECT if proven == "false-positive" else NOT_A_DEFECT,
                f"scan already resolved this observation as {proven!r}",
            )

    # -- E0a: is there any claim here at all? --------------------------------
    # Absence of a file/line anchor is not sufficient grounds to dismiss an item:
    # substantive findings are routinely stated at review level, and owner review
    # bodies that assert a blocker must still reach the owner-disposition rule.
    if not is_substantive_claim(message)[0]:
        # A grouped/summary body carries its findings inside the markup that the
        # chrome test removes. Those are real claims and they are adjudicated
        # individually as derived claims, so the container is recorded as a
        # container -- never as a body that asserted nothing.
        carried, detail = derived_claims(observation, observation_id)
        if carried:
            return build(
                RULE_SUMMARY_CONTAINER,
                EXCLUDED_SUMMARY_CONTAINER,
                NOT_A_DEFECT,
                f"summary/container body asserting nothing on its own; {detail}",
                # The container's own text is chrome plus quoted findings, so its
                # invariant and symbols would be an average of claims that belong
                # to the derived rows. They are carried there instead.
                invariant_text="",
                claimed_symbols=(),
                extra={
                    "derived_claim_count": len(carried),
                    "derived_claim_ids": [str(claim["observation_id"]) for claim in carried],
                    "derived_grammar": sorted({str(claim["grammar"]) for claim in carried}),
                    "derived_extraction": detail,
                },
            )
        return build(
            base.RULE_NO_LOCATION_ANCHOR,
            EXCLUDED_NOT_A_CLAIM,
            NOT_A_DEFECT,
            f"no substantive claim: {is_substantive_claim(message)[1]}; {detail}",
        )

    # -- E1/E4: repository-owner disposition ---------------------------------
    if owner_match is not None:
        authority = f"{owner_match['source']}:{owner_match['match']}"
        if owner_match["kind"] == "rejection":
            return build(
                RULE_OWNER_REJECTION,
                EXCLUDED_FALSE_POSITIVE,
                CLAIM_WAS_INCORRECT,
                f"repository owner rejected the claim ({authority})",
                owner_authority=authority,
            )
        family = base._match_family(invariant, ([path] if path else []), families)
        if family:
            return build(
                RULE_CANONICAL_INVARIANT_MATCH,
                EXISTING_FAMILY,
                DEFECT_WAS_REAL_AND_CORRECTED,
                f"owner disposition ({authority}); invariant matches canonical family {family}",
                claimed=family,
                fix_reference=owner_match.get("fix_reference", ""),
                owner_authority=authority,
            )
        verified = _verifiable_fix(owner_match, anchor_info, signals)
        detail = f"; {verified}" if verified else ""
        return build(
            RULE_OWNER_DISPOSITION,
            CONFIRMED,
            DEFECT_WAS_REAL_AND_CORRECTED,
            f"repository owner recorded a correction ({authority}){detail}",
            fix_reference=owner_match.get("fix_reference", ""),
            owner_authority=authority,
        )

    # -- E5: non-behavioral observation --------------------------------------
    if _NON_BEHAVIORAL.search(base.clean_comment_text(message)):
        return build(
            RULE_NON_BEHAVIORAL,
            EXCLUDED_STYLE,
            NOT_A_DEFECT,
            "claim is about naming, wording, or formatting rather than behavior",
        )

    # -- E6: external availability event -------------------------------------
    if _EXTERNAL_EVENT.search(base.clean_comment_text(message)) and not signals["symbols_surviving"]:
        return build(
            RULE_EXTERNAL_EVENT,
            INFRASTRUCTURE_OR_PROVIDER,
            NOT_APPLICABLE,
            "claim is about external availability, quota, or a transient event, " "not a Hunter implementation defect",
        )

    # -- E2: exact canonical invariant match, independently confirmed ---------
    family = base._match_family(invariant, ([path] if path else []), families)
    if family and signals["regression_test_added"]:
        return build(
            RULE_CANONICAL_INVARIANT_MATCH,
            EXISTING_FAMILY,
            DEFECT_WAS_REAL_AND_CORRECTED,
            f"invariant matches canonical family {family} and the base carries a "
            f"regression test for {', '.join(signals['regression_test_symbols'])}",
            claimed=family,
        )

    # -- E3: corrected with a verifiable fix --------------------------------
    # A merged PR plus a test that merely mentions the symbol is not proof the
    # claim was a real defect: a field name appears in both its own model and
    # any test touching that model. Confirmation therefore requires either an
    # owner-cited fix that resolves to a real commit, or a post-review change to
    # the reviewed path whose subject records the correction.
    cited = _verifiable_fix(owner_match, anchor_info, signals)
    if cited:
        return build(
            RULE_CORRECTED_WITH_TRACEABLE_FIX,
            CONFIRMED,
            DEFECT_WAS_REAL_AND_CORRECTED,
            f"corrected with traceable evidence: {cited}",
            fix_reference=cited,
        )

    # -- E7: the implementation was never shipped ----------------------------
    if not pr_merged and not signals["path_exists_at_base"]:
        return build(
            RULE_IMPLEMENTATION_ABANDONED,
            EXCLUDED_OBSOLETE_OR_SUPERSEDED,
            NOT_APPLICABLE,
            f"PR was never merged (state={pr_state or 'unknown'}) and the reviewed path "
            "does not exist on the integration base",
        )
    if not pr_merged and symbols and not signals["symbols_surviving"]:
        return build(
            RULE_IMPLEMENTATION_ABANDONED,
            EXCLUDED_OBSOLETE_OR_SUPERSEDED,
            NOT_APPLICABLE,
            f"PR was never merged (state={pr_state or 'unknown'}) and none of the claimed "
            "symbols exist on the integration base",
        )

    # -- E8: superseded by a later rewrite -----------------------------------
    if not signals["path_exists_at_base"] and symbols:
        return build(
            RULE_IMPLEMENTATION_SUPERSEDED,
            EXCLUDED_OBSOLETE_OR_SUPERSEDED,
            NOT_APPLICABLE,
            "the reviewed path no longer exists on the integration base",
        )
    # Symbols absent while the file survives is genuinely ambiguous: the code
    # may have been corrected, or rewritten into something else. Repository
    # history alone cannot tell those apart, so this is not decided here.

    # -- E9: repository history cannot establish the disposition --------------
    reasons: list[str] = []
    if not owner_match:
        reasons.append("no repository-owner disposition addresses this claim")
    if signals["symbols_surviving"]:
        reasons.append("the claimed symbols still exist on the integration base")
    else:
        reasons.append("no claim symbol could be located in the reviewed path")
    if not signals["regression_test_added"]:
        reasons.append("no regression test on the base references the claimed symbols")
    if not pr_merged:
        reasons.append(f"PR state is {pr_state or 'unknown'}")
    return build(
        RULE_INSUFFICIENT_EVIDENCE,
        OWNER_REQUIRED,
        UNDETERMINED,
        "bounded reconstruction could not establish a disposition: " + "; ".join(reasons),
    )


def _content_words(text: str) -> frozenset[str]:
    return frozenset(word for word in re.findall(r"[a-z0-9_]+", text.lower()) if len(word) > 3)


# A reviewer can post the same finding twice: once as its own comment and once
# inside the body it groups. The grouped copy is the same claim, so deriving it
# would inflate the owner queue with a claim already adjudicated.
#
# Measured over the frozen corpus, the carried text of a real twin contains 0.89
# of its twin's words, while genuinely different findings that happen to land on
# the same line reach at most 0.44. Overlap is normalized by the shorter text, so
# what is measured is "the twin says nothing the carried claim does not" rather
# than "both texts are the same size and shape".
_DUPLICATE_TEXT_CONTAINMENT = 0.8


def _duplicate_sibling(claim: dict[str, Any], siblings: Iterable[dict[str, Any]]) -> str:
    """The observation that already carries this claim verbatim, if any.

    The anchor must match exactly, path and line both. A shared topic, a summary
    comment listing several findings, or the reviewer's own repeated boilerplate
    is not a duplicate, and suppressing those would delete real claims.
    """
    claim_words = _content_words(claim["claim_text"])
    if not claim_words:
        return ""
    for sibling in siblings:
        observation = sibling.get("observation") or {}
        if str(observation.get("path") or "") != claim["path"]:
            continue
        if observation.get("line") != claim["line"]:
            continue
        words = _content_words(base.clean_comment_text(observation.get("message") or ""))
        if not words:
            continue
        if len(claim_words & words) / min(len(claim_words), len(words)) >= _DUPLICATE_TEXT_CONTAINMENT:
            return str(sibling.get("observation_id") or "")
    return ""


def _derived_observation(parent: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    """The child observation a derived claim is adjudicated as.

    Everything except the claim itself is inherited from the parent: the claim was
    made by the same reviewer, on the same reviewed head, in the same pull
    request, and the bounded-history evidence it is judged against is the same
    diff. The parent's own scan verdict is *not* inherited -- it classified the
    container, not this finding.
    """
    return {
        **{
            key: value
            for key, value in parent.items()
            if key not in ("classification", "claimed_family_id", "invariant", "derived_from")
        },
        "observation_id": claim["observation_id"],
        "path": claim["path"],
        "line": claim["line"],
        "line_end": claim["line_end"],
        "message": claim["claim_text"],
        "derived_from": {
            "parent_observation_id": claim["parent_observation_id"],
            "parent_event_id": parent.get("event_id"),
            "parent_reviewer": parent.get("reviewer"),
            "parent_reviewed_head_sha": parent.get("reviewed_head_sha"),
            "parent_source": parent.get("source"),
            "block_index": claim["block_index"],
            "grammar": claim["grammar"],
            "anchor_path": claim["path"],
            "anchor_line": claim["line"],
            "anchor_line_end": claim["line_end"],
            "extraction_rule": DERIVED_CLAIM_ID_PREFIX,
            # The claim verbatim, so the row can be reviewed on its own evidence
            # rather than on a disposition string that summarizes it.
            "claim_text": claim["claim_text"],
        },
    }


def expand_derived_claims(
    reconstructions: Sequence[Reconstruction],
    record: dict[str, Any],
    *,
    owner_login: str,
    history: GitHistory,
    families: Sequence[dict[str, Any]],
    anchors: dict[int, str],
) -> tuple[list[Reconstruction], list[Reconstruction]]:
    """Adjudicate every claim carried by a summary/container body.

    Returns ``(top_level, derived)``. The parent keeps its own place in the frozen
    ledger's item count, and each derived claim is adjudicated on its own text and
    anchor through the identical rule order, so a claim inside a container is
    disposed of exactly as it would have been had the reviewer posted it on its
    own.
    """
    items_by_id = {
        str(item.get("observation_id") or ""): item for item in (record.get("ledger") or {}).get("items") or []
    }
    ledger_items = list(items_by_id.values())
    derived: list[Reconstruction] = []
    updated: list[Reconstruction] = []
    for parent in reconstructions:
        item = items_by_id.get(parent.observation_id) if parent.rule == RULE_SUMMARY_CONTAINER else None
        if item is None:
            updated.append(parent)
            continue
        observation = item.get("observation") or {}
        carried, _detail = derived_claims(observation, parent.observation_id)
        siblings = [
            sibling for sibling in ledger_items if str(sibling.get("observation_id") or "") != parent.observation_id
        ]
        duplicates: dict[str, str] = {}
        fresh: list[dict[str, Any]] = []
        for claim in carried:
            twin = _duplicate_sibling(claim, siblings)
            if twin:
                duplicates[claim["observation_id"]] = twin
            else:
                fresh.append(claim)
        if duplicates:
            parent = replace(
                parent,
                evidence_signals={
                    **parent.evidence_signals,
                    "derived_claim_count": len(fresh),
                    "derived_claim_duplicates": duplicates,
                },
            )
        updated.append(parent)
        for claim in fresh:
            child = _derived_observation(observation, claim)
            decided = reconstruct(
                child,
                str(item.get("state") or ""),
                record,
                observation_id=str(claim["observation_id"]),
                owner_login=owner_login,
                history=history,
                families=families,
                anchors=anchors,
            )
            derived.append(
                replace(
                    decided,
                    evidence_signals={
                        **decided.evidence_signals,
                        "derived_block_index": claim["block_index"],
                        "derived_grammar": claim["grammar"],
                    },
                )
            )
    return updated, derived


# ---------------------------------------------------------------------------
# Family clustering: invariant and root cause, never wording
# ---------------------------------------------------------------------------

# The requirement a claim makes, classified by the boundary it constrains. This
# is the structural shape of the invariant: which boundary must hold. Two
# manifestations of the same invariant share the same constraint class and the
# same execution boundary, and are therefore one family.
_CONSTRAINT_CLASS = re.compile(
    r"\b(must not (?:be )?(?:silently )?"
    r"|must always|must reject|must validate|must enforce|must bound|must require"
    r"|must be (?:atomic|idempotent|deterministic|explicit|traceable|reversible)"
    r"|must (?:preserve|retain|keep)|no longer|at most|exactly one"
    r"|cannot|must fail|closed|prevent)\b",
    re.I,
)

_CONSTRAINT_CLASSES = (
    "rejects-unvalidated-input",
    "bounds-scope-or-window",
    "atomic-or-idempotent",
    "explicit-not-silent",
    "traceable-or-reproducible",
    "preserves-guarantee",
    "prevents-recurrence",
)


def constraint_class(invariant: str) -> str:
    """Which kind of boundary the claim says must hold."""
    text = invariant.lower()
    if re.search(r"\b(reject|refuse|validat|malformed|invalid|unvalidated|accepts? an? )\b", text):
        return "rejects-unvalidated-input"
    if re.search(r"\b(bound|scope|window|limit|cap|truncat|clamp|exceed|overflow)\b", text):
        return "bounds-scope-or-window"
    if re.search(r"\b(atomic|idempotent|partial|half-applied|rollback|race|concurren)\b", text):
        return "atomic-or-idempotent"
    if re.search(r"\b(silent|explicit|honest|transparent|disclose|claim(?:s|ed)? (?:to|that))\b", text):
        return "explicit-not-silent"
    if re.search(r"\b(traceab|reproduc|deterministic|evidence|regression|replay|audit)\b", text):
        return "traceable-or-reproducible"
    if re.search(r"\b(preserve|retain|keep|invariant must|still hold|survives)\b", text):
        return "preserves-guarantee"
    if re.search(r"\b(prevent|recurr|regress|guard|enforce)\b", text):
        return "prevents-recurrence"
    return "unclassified-constraint"


def cluster_key(reconstruction: Reconstruction) -> tuple[str, str]:
    """Cluster identity for a confirmed defect.

    Deliberately *not* wording, path, reviewer, PR, or UI text. Two findings
    cluster together when they violate the same class of constraint at the same
    execution boundary: that is the invariant, expressed structurally, so many
    historical manifestations collapse onto one family instead of minting
    duplicates.
    """
    return (reconstruction.execution_boundary, constraint_class(reconstruction.invariant))


@dataclass
class FamilyProposal:
    """A candidate canonical family. Never written to the registry here."""

    cluster_key: tuple[str, str]
    execution_boundary: str
    constraint_class: str
    member_count: int = 0
    manifests: list[dict[str, Any]] = field(default_factory=list)
    invariant_candidates: list[str] = field(default_factory=list)
    prevention_surface: list[str] = field(default_factory=list)
    mapped_to_existing: str | None = None

    def to_json(self) -> dict[str, Any]:
        distinct = len(self.invariant_candidates)
        return {
            "clustering_status": "unverified-hypothesis",
            "clustering_evidence": self.clustering_evidence(distinct),
            "execution_boundary": self.execution_boundary,
            "constraint_class": self.constraint_class,
            "manifest_count": self.member_count,
            "mapped_to_existing_family": self.mapped_to_existing,
            "prevention_surface": self.prevention_surface,
            "distinct_invariant_wording": distinct,
            "example_invariant": self.invariant_candidates[0] if self.invariant_candidates else "",
            "manifestations": self.manifests,
        }

    def clustering_evidence(self, distinct: int) -> str:
        """Why this bucket is not yet a family, stated as evidence not opinion.

        A real recurring family shares one violated invariant across its
        manifestations, so its members cluster on root cause rather than on
        wording. A bucket whose members are almost all worded differently is a
        keyword bucket, and presenting it as a family would invent recurrence
        that the evidence does not show.
        """
        if self.member_count <= 1:
            return "singleton: no recurrence demonstrated"
        if self.constraint_class == "unclassified-constraint":
            return "no shared violated constraint was extracted from the findings"
        if distinct >= max(3, int(self.member_count * 0.6)):
            return (
                f"{distinct} of {self.member_count} members are worded differently: "
                "no single root cause is demonstrated"
            )
        return "shared wording present, but the root cause and prevention surface are unverified"


def build_family_proposals(
    reconstructions: Iterable[Reconstruction],
) -> tuple[dict[str, list[str]], list[FamilyProposal]]:
    """Separate existing-family mappings from new-family proposals.

    Returns ``(existing_mappings, proposals)``. Confirmed items already carrying
    a canonical family are mappings, not proposals; the rest are clustered so
    that repeated manifestations of one invariant yield one proposal.
    """
    existing: dict[str, list[str]] = {}
    clusters: dict[tuple[str, str], FamilyProposal] = {}
    for item in reconstructions:
        if item.disposition == EXISTING_FAMILY and item.claimed_family_id:
            existing.setdefault(item.claimed_family_id, []).append(item.observation_id)
            continue
        if item.disposition != CONFIRMED:
            continue
        key = cluster_key(item)
        proposal = clusters.get(key)
        if proposal is None:
            proposal = FamilyProposal(
                cluster_key=key,
                execution_boundary=item.execution_boundary,
                constraint_class=constraint_class(item.invariant),
            )
            clusters[key] = proposal
        proposal.member_count += 1
        if len(proposal.manifests) < 8:
            proposal.manifests.append(
                {
                    "observation_id": item.observation_id,
                    "source_pr": item.source_pr,
                    "path": item.path,
                    "symbols": list(item.symbols[:3]),
                    "invariant": item.invariant[:240],
                }
            )
        if item.invariant and item.invariant not in proposal.invariant_candidates:
            proposal.invariant_candidates.append(item.invariant)
        for surface in item.applicability_surface:
            if surface not in proposal.prevention_surface:
                proposal.prevention_surface.append(surface)
    ordered = sorted(clusters.values(), key=lambda p: (-p.member_count, p.execution_boundary, p.constraint_class))
    return existing, ordered


# ---------------------------------------------------------------------------
# Owner-required grouping: by evidence gap, not by claim wording
# ---------------------------------------------------------------------------

_EVIDENCE_GAP = (
    ("no-owner-disposition", "no repository-owner disposition addresses the claim"),
    ("symbol-survives-unresolved", "the claimed symbols still exist and nothing records a verdict"),
    ("symbol-not-locatable", "the claim names no symbol that can be located in the reviewed path"),
    ("no-regression-test", "no regression test on the integration base references the claimed symbols"),
    ("pr-not-merged", "the pull request was never merged"),
)


def evidence_gap(reconstruction: Reconstruction) -> list[str]:
    """Which bounded evidence is missing, as a fixed vocabulary."""
    gaps: list[str] = []
    text = reconstruction.evidence
    if "no repository-owner disposition" in text:
        gaps.append("no-owner-disposition")
    if "symbols still exist" in text:
        gaps.append("symbol-survives-unresolved")
    if "no claim symbol could be located" in text:
        gaps.append("symbol-not-locatable")
    if "no regression test" in text:
        gaps.append("no-regression-test")
    if not reconstruction.pr_merged:
        gaps.append("pr-not-merged")
    return gaps or ["unclassified"]


def build_owner_required_table(reconstructions: Iterable[Reconstruction], limit_per_gap: int = 5) -> dict[str, Any]:
    """Compact owner decision table.

    Grouped by the *kind of missing evidence*, not by claim wording, so the
    owner decides a small number of evidence policies instead of hundreds of
    individual findings. Representative examples are included per group.
    """
    buckets: dict[tuple[str, ...], list[Reconstruction]] = {}
    for item in reconstructions:
        if item.disposition != OWNER_REQUIRED:
            continue
        buckets.setdefault(tuple(evidence_gap(item)), []).append(item)
    groups: list[dict[str, Any]] = []
    for gaps, members in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        descriptions = [text for key, text in _EVIDENCE_GAP if key in gaps]
        prs = sorted({member.source_pr for member in members})
        groups.append(
            {
                "evidence_gaps": list(gaps),
                "evidence_gap_description": "; ".join(descriptions) or "unclassified",
                "item_count": len(members),
                "pr_count": len(prs),
                "prs": prs[:20],
                "why_owner_authority_required": (
                    "Repository history cannot distinguish a real surviving defect from an "
                    "incorrect claim here: no repository-owned artifact records a verdict for "
                    "this claim, so only the owner can establish whether the behaviour was wrong."
                ),
                "proposed_disposition": "OWNER_REQUIRED",
                "examples": [
                    {
                        "observation_id": member.observation_id,
                        "source_pr": member.source_pr,
                        "finding": member.invariant[:220],
                        "path": member.path,
                        "line": member.line,
                        "evidence_for_defect": (
                            f"claimed symbols still present on the integration base: "
                            f"{', '.join(member.symbols_surviving[:4])}"
                            if member.symbols_surviving
                            else "claim names a concrete code location that existed at review time"
                        ),
                        "evidence_against": (
                            "no regression test, guard, or later commit on the reviewed path " "records a correction"
                        ),
                    }
                    for member in members[:limit_per_gap]
                ],
            }
        )
    return {
        "owner_required_count": sum(len(members) for members in buckets.values()),
        "group_count": len(groups),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Strict reconciliation
# ---------------------------------------------------------------------------


def build_reconstruction_manifest(
    *,
    repository: str,
    data_dir: Path,
    reconstructions: Sequence[Reconstruction],
    scan_statused: int,
    scan_total: int,
    existing_mappings: dict[str, list[str]],
    proposals: Sequence[FamilyProposal],
    owner_table: dict[str, Any],
    non_recurrence: dict[str, Any] | None = None,
    evidenced_families: Sequence[EvidencedFamily] | None = None,
    derived: Sequence[Reconstruction] = (),
) -> dict[str, Any]:
    """Strict closure metrics over the reconstructed dispositions.

    ``coverage_gap`` is zero only when every dimension is zero. Scan completion
    alone can never produce a zero ``coverage_gap``, and neither can a run in
    which the owner still has to decide.

    Derived claims are counted apart from the frozen ledger's items and then added
    into the dimensions they really belong to. They are real findings recovered
    from summary bodies, so leaving them out of ``canonical_mapping_gap`` and
    ``unresolved_evidence_count`` would understate the gap; counting them as
    ledger items would break exact reconciliation against a ledger they are
    deliberately not in.
    """
    by_disposition = {
        CONFIRMED: 0,
        RESOLVED_NON_RECURRING: 0,
        PROPOSED_NEW_FAMILY: 0,
        EXISTING_FAMILY: 0,
        EXCLUDED_FALSE_POSITIVE: 0,
        EXCLUDED_STYLE: 0,
        EXCLUDED_OBSOLETE_OR_SUPERSEDED: 0,
        INFRASTRUCTURE_OR_PROVIDER: 0,
        OWNER_REQUIRED: 0,
        EXCLUDED_NOT_A_CLAIM: 0,
        EXCLUDED_SUMMARY_CONTAINER: 0,
    }
    by_rule: dict[str, int] = {}
    by_truth: dict[str, int] = {}
    for item in reconstructions:
        by_disposition[item.disposition] = by_disposition.get(item.disposition, 0) + 1
        by_rule[item.rule] = by_rule.get(item.rule, 0) + 1
        by_truth[item.historical_defect_truth] = by_truth.get(item.historical_defect_truth, 0) + 1

    derived_by_disposition: dict[str, int] = {}
    derived_by_rule: dict[str, int] = {}
    derived_by_truth: dict[str, int] = {}
    for item in derived:
        derived_by_disposition[item.disposition] = derived_by_disposition.get(item.disposition, 0) + 1
        derived_by_rule[item.rule] = derived_by_rule.get(item.rule, 0) + 1
        derived_by_truth[item.historical_defect_truth] = derived_by_truth.get(item.historical_defect_truth, 0) + 1

    # A claim a reviewer posted twice -- once alone, once inside the body it
    # grouped -- is one finding. Recording the twin keeps the suppression
    # auditable instead of letting the owner queue quietly absorb both copies.
    duplicate_claims = [
        {
            "container_observation_id": item.observation_id,
            "carried_claim_id": carried_id,
            "already_observed_as": twin,
        }
        for item in reconstructions
        for carried_id, twin in sorted((item.evidence_signals.get("derived_claim_duplicates") or {}).items())
    ]

    ledger_total = base.load_item_total(data_dir)
    adjudicated = len(reconstructions)
    owner_required = by_disposition[OWNER_REQUIRED]
    derived_total = len(derived)
    derived_confirmed = derived_by_disposition.get(CONFIRMED, 0)
    derived_owner_required = derived_by_disposition.get(OWNER_REQUIRED, 0)

    scan_coverage_gap = scan_total - scan_statused
    adjudication_coverage_gap = ledger_total - adjudicated
    # A confirmed defect still lacking a canonical family is unmapped history.
    # A non-recurring one-off needs no family, so it is not unmapped history and
    # does not count here; a confirmed finding that is awaiting a family does.
    canonical_mapping_gap = by_disposition[CONFIRMED] + derived_confirmed
    # Reconstructed but the owner must still decide.
    unresolved_evidence_count = owner_required + derived_owner_required
    coverage_gap = scan_coverage_gap + adjudication_coverage_gap + canonical_mapping_gap + unresolved_evidence_count
    disposition_sum = sum(by_disposition.values())
    derived_sum = sum(derived_by_disposition.values())

    return {
        "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
        "repository": repository,
        "scan": {
            "total_prs": scan_total,
            "statused_prs": scan_statused,
            "scan_coverage_gap": scan_coverage_gap,
        },
        "reconciliation": {
            "raw_scan_ledger_items": ledger_total,
            "reconstructed_items": adjudicated,
            "disposition_sum": disposition_sum,
            "reconciles_exactly": disposition_sum == ledger_total and adjudicated == ledger_total,
            "derived_claims": derived_total,
            "derived_claim_sum": derived_sum,
            "derived_claims_reconcile_exactly": derived_sum == derived_total,
            "summary_container_bodies": by_rule.get(RULE_SUMMARY_CONTAINER, 0),
        },
        "dispositions": by_disposition,
        "rules": dict(sorted(by_rule.items())),
        "historical_defect_truth": dict(sorted(by_truth.items())),
        "scan_coverage_gap": scan_coverage_gap,
        "adjudication_coverage_gap": adjudication_coverage_gap,
        "canonical_mapping_gap": canonical_mapping_gap,
        "unresolved_evidence_count": unresolved_evidence_count,
        "coverage_gap": coverage_gap,
        "family_proposals": {
            "existing_family_mappings": {key: len(value) for key, value in sorted(existing_mappings.items())},
            "mapped_item_count": sum(len(value) for value in existing_mappings.values()),
            "new_family_proposal_count": len(proposals),
            "proposed_families": [proposal.to_json() for proposal in proposals],
        },
        "owner_required": {
            "count": owner_required + derived_owner_required,
            "ledger_item_count": owner_required,
            "derived_claim_count": derived_owner_required,
            "group_count": owner_table["group_count"],
        },
        "derived_claims": {
            "count": derived_total,
            "parent_container_bodies": by_rule.get(RULE_SUMMARY_CONTAINER, 0),
            "note": (
                "Claims recovered from grouped/summary review bodies, adjudicated through "
                "the identical rule order with their own anchor. The frozen ledger is "
                "unchanged: these are a second, additive view of observations that already "
                "exist, with stable identities and exact parent provenance."
            ),
            "dispositions": dict(sorted(derived_by_disposition.items())),
            "rules": dict(sorted(derived_by_rule.items())),
            "historical_defect_truth": dict(sorted(derived_by_truth.items())),
            "duplicate_of_existing_observation_count": len(duplicate_claims),
            "duplicate_of_existing_observations": duplicate_claims,
        },
        "non_recurrence_resolution": non_recurrence or {},
        "evidenced_families": {
            "count": len(evidenced_families or ()),
            "families": [family.to_json() for family in (evidenced_families or ())],
        },
    }


# A PR record is terminal when the scanner recorded a terminal scan state.
_TERMINAL_SCAN_STATES = frozenset({"complete"})


def scan_status_counts(data_dir: Path) -> tuple[int, int]:
    """``(statused_prs, total_prs)`` derived from persisted records.

    Derived rather than assumed: the snapshot length is the denominator, and
    the numerator counts only PR records the scanner actually marked terminal.
    Reading this from disk is what lets a missing record show up as scan
    coverage instead of being silently rounded away.
    """
    snapshot = base.read_json_file(data_dir / "snapshot.json", required=True)
    assert isinstance(snapshot, dict)
    total = len(snapshot.get("prs") or [])
    statused = 0
    for path in sorted((data_dir / "prs").glob("*.json")):
        record = base.read_json_file(path, required=True)
        if isinstance(record, dict) and str(record.get("scan_state") or "") in _TERMINAL_SCAN_STATES:
            statused += 1
    return statused, total


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def squash_anchors(repo_root: Path, ref: str = "origin/main") -> dict[int, str]:
    """Map PR number to the commit that landed it.

    PR head commits are unreachable in a squash-merged clone, so the squash
    commit on the integration base is the anchor for "what happened after this
    review".
    """
    proc = subprocess.run(
        ["git", "log", "--format=%H%x1f%s", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    anchors: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        head, _, subject = line.partition("\x1f")
        for match in re.finditer(r"\(#(\d+)\)", subject):
            anchors.setdefault(int(match.group(1)), head)
    return anchors


def run_reconstruction(
    *,
    data_dir: Path,
    registry_path: Path,
    owner_login: str,
    repository: str,
    repo_root: Path,
    ref: str = "origin/main",
    resume: bool = True,
    recurrence_verdicts: dict[str, Any] | None = None,
    invariant_verdicts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconstruct dispositions for every ledger item, from local evidence only.

    The frozen scan is read, never written: no re-fetch, no rescan, no
    checkpoint mutation, and no write to canonical history. Per-PR results are
    cached so an interrupted run resumes, and the cache is keyed on the engine
    and registry digests so a rule change can never be masked by stale results.
    """
    families = base._family_index(registry_path)
    registry_digest = base.stable_json_digest({"families": families})
    rules_digest = base.engine_digest() + RECONSTRUCTION_SCHEMA_VERSION
    history = GitHistory(repo_root, ref)
    anchors = squash_anchors(repo_root, ref)
    recurrence_verdicts = recurrence_verdicts or {}

    out_dir = data_dir / "reconstruction"
    out_dir.mkdir(parents=True, exist_ok=True)
    reconstructions: list[Reconstruction] = []
    derived_reconstructions: list[Reconstruction] = []
    per_pr: dict[int, list[Reconstruction]] = {}
    records: dict[int, dict[str, Any]] = {}
    reused = 0
    for pr_number, record in base.iter_ledger_items(data_dir):
        target = out_dir / f"{pr_number}.json"
        if resume and target.is_file():
            cached = base.read_json_file(target, required=True)
            if (
                isinstance(cached, dict)
                and cached.get("registry_digest") == registry_digest
                and cached.get("engine_digest") == rules_digest
            ):
                restored = [Reconstruction.from_json(entry) for entry in cached["reconstructions"]]
                top_level = [item for item in restored if not item.derived_from]
                reconstructions.extend(top_level)
                derived_reconstructions.extend(item for item in restored if item.derived_from)
                per_pr[pr_number] = restored
                records[pr_number] = record
                reused += 1
                continue
        decided = [
            reconstruct(
                item["observation"],
                str(item.get("state") or ""),
                record,
                observation_id=str(item.get("observation_id") or ""),
                owner_login=owner_login,
                history=history,
                families=families,
                anchors=anchors,
            )
            for item in (record.get("ledger") or {}).get("items") or []
        ]
        decided, carried = expand_derived_claims(
            decided,
            record,
            owner_login=owner_login,
            history=history,
            families=families,
            anchors=anchors,
        )
        base.atomic_write_json(
            target,
            {
                "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
                "source_pr": pr_number,
                "registry_digest": registry_digest,
                "engine_digest": rules_digest,
                "reconstructions": [item.to_json() for item in decided + carried],
            },
        )
        reconstructions.extend(decided)
        derived_reconstructions.extend(carried)
        per_pr[pr_number] = decided + carried
        records[pr_number] = record

    # Every claim participates in the whole-population passes below, derived
    # claims included: a finding recovered from a summary body is a finding, and
    # leaving it out of recurrence and family work would resolve the rest of the
    # population against an incomplete set.
    all_items = reconstructions + derived_reconstructions

    # Recurrence is a property of the whole confirmed population, so the
    # non-recurrence pass runs once, over every confirmed finding, before any
    # family grouping. A finding that still needs a family must be CONFIRMED
    # here, or the family work would be silently skipped.
    extractions: dict[str, Any] = {}
    for pr_number, decided in per_pr.items():
        record = records.get(pr_number) or {}
        for item in decided:
            extractions[item.observation_id] = semantic_invariant_extraction.extract(item, record, history, owner_login)
    extractions = semantic_invariant_extraction.apply_verdicts(extractions, invariant_verdicts or {})
    clusters = semantic_invariant_extraction.recurrence_clusters(*_confirmed_recurrence_view(all_items, extractions))
    clusters = _apply_recurrence_verdicts(clusters, recurrence_verdicts)
    # Families found by review rather than by a weak signal are declared
    # explicitly; the signals above are never allowed to invent one.
    clusters = clusters + semantic_invariant_extraction.declared_clusters(recurrence_verdicts or {})
    # A verified shared invariant maps to a family *before* the non-recurrence
    # pass runs, otherwise a finding that needs a family could be resolved as a
    # one-off and the family work would be silently skipped.
    all_items, evidenced_families = apply_family_mappings(
        all_items,
        extractions,
        clusters,
        frozenset(str(f.get("id") or "") for f in families),
    )
    all_items, non_recurrence = resolve_non_recurring(all_items, extractions, clusters)
    # The two passes above can change a derived claim's disposition, so the split
    # back into ledger items and derived claims is made after them, by identity.
    derived_ids = {item.observation_id for item in derived_reconstructions}
    reconstructions = [item for item in all_items if item.observation_id not in derived_ids]
    derived_reconstructions = [item for item in all_items if item.observation_id in derived_ids]

    # The per-PR file is the durable per-finding disposition, and the two passes
    # above change those dispositions. Writing it only before them would leave
    # every per-PR record disagreeing with the manifest, so it is rewritten from
    # the final state. Re-running is idempotent: both passes only act on
    # CONFIRMED findings, so a resumed run cannot map or resolve one twice.
    final_by_pr: dict[int, list[dict[str, Any]]] = {}
    for item in all_items:
        final_by_pr.setdefault(item.source_pr, []).append(item.to_json())
    for pr_number, entries in sorted(final_by_pr.items()):
        base.atomic_write_json(
            out_dir / f"{pr_number}.json",
            {
                "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
                "source_pr": pr_number,
                "registry_digest": registry_digest,
                "engine_digest": rules_digest,
                "reconstructions": entries,
            },
        )

    # A recovered claim is owner work and family work exactly as a posted one is,
    # so both tables are built over the whole population, derived claims included.
    existing_mappings, proposals = build_family_proposals(all_items)
    owner_table = build_owner_required_table(all_items)
    base.atomic_write_json(data_dir / "reconstruction" / "owner_required_table.json", owner_table)
    base.atomic_write_json(
        data_dir / "reconstruction" / "family_proposals.json",
        {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "existing_family_mappings": {key: value for key, value in sorted(existing_mappings.items())},
            "new_family_proposals": [proposal.to_json() for proposal in proposals],
        },
    )
    base.atomic_write_json(
        data_dir / "reconstruction" / "invariant_extractions.json",
        {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "note": (
                "Typed evidence per finding: the rule the finding itself asserts, the "
                "owner's causal statement, the real enclosing symbol, the applicability "
                "conditions, and the prevention evidence. Recorded even when the finding "
                "never needed a family, because it is the only characterization of the "
                "violated invariant that exists."
            ),
            "extractions": {k: v.to_json() for k, v in sorted(extractions.items())},
        },
    )
    base.atomic_write_json(
        data_dir / "reconstruction" / "evidenced_families.json",
        {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "note": (
                "Families built only from recorded same-invariant verdicts over verified "
                "invariants. Every required field is carried per family; a proposal that "
                "cannot carry them is not produced."
            ),
            "count": len(evidenced_families),
            "families": [family.to_json() for family in evidenced_families],
        },
    )
    base.atomic_write_json(
        data_dir / "reconstruction" / "recurrence_clusters.json",
        {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "note": (
                "Weak signals that surface possible recurrence for semantic review. "
                "same_invariant is a recorded verdict, never inferred from wording or "
                "shared code location."
            ),
            "unreviewed_cluster_count": sum(1 for c in clusters if c.same_invariant is None),
            "same_invariant_cluster_count": sum(1 for c in clusters if c.same_invariant),
            "different_invariant_cluster_count": sum(1 for c in clusters if c.same_invariant is False),
            "clusters": [c.to_json() for c in clusters],
        },
    )
    scan_statused, scan_total = scan_status_counts(data_dir)
    manifest = build_reconstruction_manifest(
        repository=repository,
        data_dir=data_dir,
        reconstructions=reconstructions,
        scan_statused=scan_statused,
        scan_total=scan_total,
        existing_mappings=existing_mappings,
        proposals=proposals,
        owner_table=owner_table,
        non_recurrence=non_recurrence,
        evidenced_families=evidenced_families,
        derived=derived_reconstructions,
    )
    # The digest covers the decisions only. Folding in cache statistics would
    # make an identical reconstruction hash differently on a resumed run.
    manifest["closure_manifest_digest"] = base.stable_json_digest(
        {key: value for key, value in manifest.items() if key != "reused_pr_records"}
    )
    manifest["reused_pr_records"] = reused
    base.atomic_write_json(data_dir / "historical_closure_manifest.json", manifest)
    return manifest


def _verifiable_fix(
    owner_match: dict[str, Any] | None,
    anchor_info: dict[str, Any],
    signals: dict[str, Any],
) -> str:
    """A fix reference that cryptographically resolves to a real commit.

    Returns a short description of the verified fix, or an empty string when
    nothing verifiable exists.

    Three weaker signals are explicitly refused, each of which silently disposed
    of real findings on this ledger:

    * "The PR merged" and "a test mentions this symbol": neither shows the claim
      was a real defect.
    * A commit *subject* containing a correction keyword. A subject line is
      written for a change as a whole, not for any individual finding, so one
      generic commit ("Implement Reviewer Finding Disposition Authority ...")
      was confirming every finding in the file it touched -- 37 findings here.
    * An owner-cited SHA resolving to no commit in this pull request. Verifying
      that the referenced commit exists in the PR is the entire value of the
      citation, so an unverifiable citation is not evidence.
    """
    shas = [str(sha) for sha in anchor_info.get("commit_shas") or []]
    cited = str((owner_match or {}).get("fix_reference") or "")
    # The commit must *start with* the citation, so a longer reference pointing at
    # a different commit is rejected instead of matching on its first seven chars.
    if cited and any(sha.startswith(cited) for sha in shas):
        return f"owner cited fix {cited} which is a commit in this pull request"
    return ""


# ---------------------------------------------------------------------------
# Non-recurrence resolution
# ---------------------------------------------------------------------------


def resolve_non_recurring(
    reconstructions: Sequence[Reconstruction],
    extractions: dict[str, Any],
    clusters: Sequence[Any],
) -> tuple[list[Reconstruction], dict[str, Any]]:
    """Reclassify confirmed findings that no family is owed.

    Recurrence is a property of the whole confirmed population, so it cannot be
    decided one observation at a time. This runs as a post-pass over the full set
    and is fail-closed: a finding is only resolved when *all* of the following
    are true, and otherwise it stays ``CONFIRMED`` and keeps counting against
    ``canonical_mapping_gap``.

    1. it is ``CONFIRMED``;
    2. a violated invariant is actually documented from the evidence;
    3. the fix is traceable to a real commit, not an unverifiable reference;
    4. prevention is verified *on the integration base* - a regression test or
       guard that references the claimed symbols and whose file still exists;
    5. no shared violated invariant can be demonstrated against any other
       confirmed finding, by either recurring wording or a reviewed code
       location.

    Condition 5 is what keeps this from being a silent escape hatch. A cluster
    that no reviewer has judged is never resolved, and neither is a cluster a
    reviewer judged to be a shared invariant but that never yielded a published
    family: those members still owe a decision.
    """
    still_confirmed = {item.observation_id for item in reconstructions if item.disposition == CONFIRMED}
    awaiting_review: set[str] = set()
    for cluster in clusters:
        members = set(cluster.observation_ids)
        if cluster.same_invariant is None:
            awaiting_review.update(members)
            continue
        if cluster.same_invariant is not True:
            continue
        source_prs = {extractions[m].source_pr for m in members if m in extractions}
        if len(source_prs) < 2:
            # The same defect described twice inside one change is not a family.
            continue
        if members & still_confirmed:
            awaiting_review.update(members)

    resolved: list[str] = []
    withheld: dict[str, int] = {}

    def withhold(reason: str) -> None:
        withheld[reason] = withheld.get(reason, 0) + 1

    updated: list[Reconstruction] = []
    for item in reconstructions:
        if item.disposition != CONFIRMED:
            updated.append(item)
            continue
        extraction = extractions.get(item.observation_id)
        invariant = str(getattr(extraction, "violated_invariant", "") or "")
        if not getattr(extraction, "invariant_verified", False):
            withhold("no verified violated invariant")
            updated.append(item)
            continue
        if not _SHA_PREFIX.match(item.fix_reference or ""):
            withhold("fix is not traceable to a verified commit")
            updated.append(item)
            continue
        if not (item.regression_test_added or item.guard_added):
            withhold("no regression test or guard on the integration base")
            updated.append(item)
            continue
        if not (item.path_exists_at_base or item.symbols_surviving):
            withhold("prevention cannot be verified at the integration base")
            updated.append(item)
            continue
        if item.observation_id in awaiting_review:
            withhold("a shared violated invariant is still under review")
            updated.append(item)
            continue

        updated.append(
            replace(
                item,
                disposition=RESOLVED_NON_RECURRING,
                rule=RULE_RESOLVED_NON_RECURRING,
                historical_defect_truth=DEFECT_WAS_REAL_AND_CORRECTED,
                evidence=(
                    f"{item.evidence}; resolved as a non-recurring defect: the violated "
                    f"invariant ({invariant[:120]}) is documented, the fix is traceable to "
                    f"{item.fix_reference[:12]}, prevention is verified on the integration "
                    "base, and no shared violated invariant is demonstrable against any "
                    "other finding"
                ),
                evidence_signals={
                    **item.evidence_signals,
                    "non_recurrence_resolution": {
                        "violated_invariant": invariant,
                        "fix_commit": item.fix_reference,
                        "prevention": "regression test and guard on the integration base",
                        "recurrence": ("no shared violated invariant demonstrated against any other finding"),
                    },
                },
            )
        )
        resolved.append(item.observation_id)

    return updated, {
        "resolved_non_recurring": len(resolved),
        "resolved_observation_ids": resolved,
        "withheld_reasons": dict(sorted(withheld.items())),
        "findings_awaiting_recurrence_review": len(awaiting_review),
        "verified_invariant_count": sum(1 for e in extractions.values() if getattr(e, "invariant_verified", False)),
    }


def _apply_recurrence_verdicts(clusters: Sequence[Any], verdicts: dict[str, Any]) -> list[Any]:
    """Stamp recorded semantic verdicts onto the weak recurrence clusters.

    A cluster stays unreviewed unless a verdict names it exactly. There is no
    default: an unreviewed cluster keeps its findings out of the non-recurring
    outcome, so nothing is resolved on the strength of an unexamined signal.
    """
    applied: list[Any] = []
    verdicts = semantic_invariant_extraction.verdict_map(verdicts)
    for cluster in clusters:
        verdict = verdicts.get(f"{cluster.basis}::{cluster.key}")
        if not verdict:
            applied.append(cluster)
            continue
        applied.append(
            replace(
                cluster,
                same_invariant=bool(verdict["same_invariant"]),
                family_id=str(verdict.get("family_id") or ""),
                verdict_evidence=str(verdict.get("evidence") or ""),
            )
        )
    return applied


# ---------------------------------------------------------------------------
# Family mapping from recorded verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidencedFamily:
    """A family whose every required field is traceable to recorded evidence."""

    family_key: str
    family_id: str
    violations: int
    violated_invariant: str
    root_causes: tuple[str, ...]
    execution_boundaries: tuple[str, ...]
    applicability: tuple[str, ...]
    prevention_symbols: tuple[str, ...]
    fix_commits: tuple[str, ...]
    observation_ids: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "family_key": self.family_key,
            "family_id": self.family_id,
            "violations": self.violations,
            "violated_invariant": self.violated_invariant,
            "root_causes": list(self.root_causes),
            "execution_boundaries": list(self.execution_boundaries),
            "applicability": list(self.applicability),
            "prevention_symbols": list(self.prevention_symbols),
            "fix_commits": list(self.fix_commits),
            "observation_ids": list(self.observation_ids),
            "evidence_backed": True,
        }


def _confirmed_recurrence_view(
    reconstructions: Sequence[Reconstruction], extractions: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, tuple[str, tuple[str, ...]]]]:
    """Narrow the recurrence inputs to the findings that still owe a family.

    Only the confirmed population is waiting on a family decision. Clustering
    the whole ledger would present provider status lines, closed findings, and
    superseded rows as recurrence candidates, which both inflates the review
    surface and buries the real families under noise that cannot be mapped.
    """
    confirmed_ids = {item.observation_id for item in reconstructions if item.disposition == CONFIRMED}
    return (
        {oid: ext for oid, ext in extractions.items() if oid in confirmed_ids},
        {
            item.observation_id: (item.path or "", tuple(item.symbols or ()))
            for item in reconstructions
            if item.observation_id in confirmed_ids
        },
    )


def apply_family_mappings(
    reconstructions: Sequence[Reconstruction],
    extractions: dict[str, Any],
    clusters: Sequence[Any],
    canonical_family_ids: frozenset[str] = frozenset(),
) -> tuple[list[Reconstruction], list[EvidencedFamily]]:
    """Map confirmed findings onto families, using recorded verdicts only.

    Fail-closed: a cluster maps only when every one of its members has a
    verified invariant *and* the cluster carries a ``same_invariant`` verdict of
    true. A cluster still awaiting review, a member without a verified
    invariant, or a verdict that says the findings differ all leave every member
    ``CONFIRMED`` and counting against ``canonical_mapping_gap``.

    A verdict may name a family. That name is treated as a canonical mapping
    only when the registry already carries that id; anything else is a new
    evidence-backed proposal, so a declared slug can never be recorded as
    history it does not have.
    """
    by_id = {item.observation_id: item for item in reconstructions}
    families: list[EvidencedFamily] = []
    mapped: dict[str, EvidencedFamily] = {}

    for cluster in clusters:
        if cluster.same_invariant is not True:
            continue
        members = [by_id[oid] for oid in cluster.observation_ids if oid in by_id]
        members = [m for m in members if m.disposition == CONFIRMED]
        if len(members) < 2:
            continue
        # Recurrence means the defect came back in a later change. A finding and
        # the disposition that closed it in the same PR are one defect, so they
        # are never published as a two-member family.
        if len({m.source_pr for m in members}) < 2:
            continue
        extractions_for = [extractions.get(m.observation_id) for m in members]
        if not all(
            e is not None and getattr(e, "invariant_verified", False) and e.violated_invariant.strip()
            for e in extractions_for
        ):
            continue
        # The family's invariant is the one the members themselves state, unless
        # a reviewer declared this family and wrote the invariant down. Taking
        # the reviewer's evidence note instead would let a family be published
        # under a description no member actually asserted, so a family surfaced
        # by a weak signal only forms when its members agree on the rule.
        declared = cluster.basis == "declared" and cluster.invariants and cluster.invariants[0].strip()
        shared_invariant = {
            semantic_invariant_extraction.normalise_invariant(e.violated_invariant)  # type: ignore[union-attr]
            for e in extractions_for
        }
        if declared:
            violated_invariant = str(cluster.invariants[0]).strip()
        elif len(shared_invariant) == 1 and next(iter(shared_invariant)):
            violated_invariant = str(extractions_for[0].violated_invariant)  # type: ignore[union-attr]
        else:
            continue

        family = EvidencedFamily(
            family_key=f"{cluster.basis}::{cluster.key}",
            family_id=cluster.family_id,
            violations=len(members),
            violated_invariant=violated_invariant,
            root_causes=tuple(
                dict.fromkeys(e.root_cause for e in extractions_for if e.root_cause)  # type: ignore[union-attr]
            ),
            execution_boundaries=tuple(
                dict.fromkeys(
                    symbol
                    for e in extractions_for
                    for symbol in (e.execution_boundary or ())  # type: ignore[union-attr]
                )
            ),
            applicability=tuple(
                dict.fromkeys(
                    condition
                    for e in extractions_for
                    for condition in (e.applicability or ())  # type: ignore[union-attr]
                )
            )[:8],
            prevention_symbols=tuple(dict.fromkeys(symbol for m in members for symbol in (m.symbols or ()))),
            fix_commits=tuple(dict.fromkeys(m.fix_reference for m in members if m.fix_reference)),
            observation_ids=tuple(sorted(m.observation_id for m in members)),
        )
        families.append(family)
        for m in members:
            mapped[m.observation_id] = family

    if not mapped:
        return list(reconstructions), []

    updated: list[Reconstruction] = []
    for item in reconstructions:
        family = mapped.get(item.observation_id)
        if family is None:
            updated.append(item)
            continue
        if family.family_id and family.family_id in canonical_family_ids:
            updated.append(
                replace(
                    item,
                    disposition=EXISTING_FAMILY,
                    rule=RULE_PROPOSED_NEW_FAMILY,
                    claimed_family_id=family.family_id,
                    historical_defect_truth=DEFECT_WAS_REAL_AND_CORRECTED,
                    evidence=(
                        f"{item.evidence}; mapped to canonical family {family.family_id} on a "
                        f"recorded verdict that it violates the same invariant: "
                        f"{family.violated_invariant[:160]}"
                    ),
                    evidence_signals={
                        **item.evidence_signals,
                        "family_mapping": {
                            "family_id": family.family_id,
                            "violated_invariant": family.violated_invariant,
                            "root_causes": list(family.root_causes),
                            "execution_boundaries": list(family.execution_boundaries),
                            "applicability": list(family.applicability),
                            "prevention_symbols": list(family.prevention_symbols),
                        },
                    },
                )
            )
        else:
            updated.append(
                replace(
                    item,
                    disposition=PROPOSED_NEW_FAMILY,
                    rule=RULE_PROPOSED_NEW_FAMILY,
                    historical_defect_truth=DEFECT_WAS_REAL_AND_CORRECTED,
                    invariant=family.violated_invariant,
                    evidence=(
                        f"{item.evidence}; mapped to an evidence-backed new-family proposal on a "
                        f"recorded verdict that it violates the same invariant as "
                        f"{family.violations - 1} other finding(s): {family.violated_invariant[:160]}"
                    ),
                    evidence_signals={
                        **item.evidence_signals,
                        "family_mapping": {
                            "family_key": family.family_key,
                            "violated_invariant": family.violated_invariant,
                            "root_causes": list(family.root_causes),
                            "execution_boundaries": list(family.execution_boundaries),
                            "applicability": list(family.applicability),
                            "prevention_symbols": list(family.prevention_symbols),
                        },
                    },
                )
            )
    return updated, families
