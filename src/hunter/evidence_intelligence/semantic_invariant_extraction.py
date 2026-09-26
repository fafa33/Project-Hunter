"""Evidence-backed extraction of a violated invariant, and nothing else.

A recurring defect family is a *shared violated invariant*, not a shared
vocabulary. This module therefore refuses to produce a family from wording
similarity. For every confirmed finding it extracts, each field traceable to a
concrete artifact:

``violated_invariant``
    The rule the finding itself asserts was broken, taken from the finding's own
    rule statement (its bolded lead or imperative line). Never from the owner's
    fix text: "Fixed in the current head. The trusted entrypoint now ..." is a
    disposition, and reading it as the invariant is what turned a fix into 78
    buckets of generic prose.
``root_cause``
    The causal clause of the owner's disposition, when the owner states one.
``execution_boundary``
    The enclosing module/symbol resolved from the *real* file on the
    integration base, not from the path string.
``applicability``
    The conditions the finding says the rule binds under, with their symbols.
``prevention_mechanism``
    The regression tests on the integration base that cover the claim's symbols,
    plus the commit that carried the fix when it is verifiable.

Every field is optional and the extraction is fail-closed: an ambiguous
extraction is reported as ambiguous, and ambiguous findings are never assigned
to a family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Finding text: locating the rule the finding asserts
# ---------------------------------------------------------------------------

# A bot finding states its rule as a bolded or marked-up lead line, e.g.
#   **<sub>...</sub>  Preserve PR edits in the freshness boundary**
#   **Reject non-ASCII signatures before compare_digest**
_BOLD_LEAD = re.compile(r"\*\*(?P<lead>[^*\n]{8,200}?)\*\*")
_LEAD_NOISE = re.compile(r"(?i)\bP[0-3] badge|img\.shields\.io|<sub>|</sub>|https?://\S+|\bstyle=flat\b")
_SENTENCE = re.compile(r"(?P<s>[^.!?\n]{15,300}[.!?])")

# A rule statement is an obligation about the behaviour of this repository's
# code. Two grammatical forms qualify: a normative modal, or an imperative
# addressed to the code rather than to a person.
_NORMATIVE = re.compile(
    r"(?i)\b(?:must(?: not| never)?|should(?: not)?|never|always|only|has to|have to|"
    r"needs? to|required|requires?|cannot|can not|may not|forbidden|prohibited|"
    r"refuses? to|fails? to)\b"
)
# Instructions to a human or an agent, and progress/status narration. "Carefully
# review the code before committing" matched the old shape test on the word
# "before" and was recorded as a violated invariant, which is precisely the
# failure this module must not commit: an instruction is not an invariant, and a
# status update is not evidence of one.
_HUMAN_DIRECTED = re.compile(
    r"(?i)\b(?:carefully|please|kindly|make sure|be sure|remember|note that|"
    r"keep this thread|open until|address(?:ed)? before approval|"
    r"before committing|before reporting|commit(?:ting)? your|"
    r"i will|we will|let me|now let|as requested|great catch|thanks for)\b"
)
_STATUS = re.compile(
    r"(?i)\b(?:is green|are green|all checks|checks passed|completed|done|"
    r"verified that|confirmed that|status:|progress|update:|"
    r"fixed on|resolved on|pushed head|exact head)\b"
    # A vendor's own summary counter, e.g. "**Actionable comments posted: 1**".
    # It reports how many findings follow; it states no obligation.
    r"|comments? posted\s*:"
    # An acknowledgement plus a commit, and a test-run tally. Both are the
    # outcome of the work, not an obligation on the code: "Valid - fixed in
    # <sha>" and "4315 passed, 3 skipped, 0 failed" state no rule at all.
    r"|\bfixed in\b|\b\d+\s+(?:passed|failed|skipped)\b|^\s*valid\b|"
    r"\breview thread resolved\b|\bthanks for the\b"
)
_IMPERATIVE_SYSTEM = re.compile(
    r"(?i)^(?:reject|require|validate|enforce|preserve|propagate|canonicali[sz]e|"
    r"return|raise|ignore|skip|filter|record|emit|compare|verify|apply|bound|"
    r"derive|redact|hash|freeze|guarantee|prefer|avoid|keep|use|accept|admit|"
    r"treat|do not|don't|must|never|always|only)\b"
)


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_WS = re.compile(r"\s+")


def _clean_lead(text: str) -> str:
    stripped = _MD_IMAGE.sub(" ", text)
    stripped = _MD_LINK.sub(r"\1", stripped)
    stripped = _LEAD_NOISE.sub(" ", stripped)
    return _WS.sub(" ", stripped).strip(" *#-:")


def rule_statement(text: str | None) -> str:
    """The rule a finding asserts, or an empty string when it states none.

    A finding that only narrates a symptom, instructs a reviewer, or reports
    progress asserts no invariant, and inventing one would be the exact
    force-fit this module exists to prevent.

    What comes back is a *candidate*, not a verdict. An obligation may be
    phrased in domain language rather than in symbols, so no symbol test is
    applied, and a candidate is not evidence until a judgement is recorded for
    it. Instructions to a person and progress narration are rejected outright
    because they are not claims about the behaviour of this code at all.
    """
    body = (text or "").strip()
    if not body:
        return ""
    for candidate in _rule_candidates(body):
        return candidate
    return ""


def _rule_candidates(body: str) -> list[str]:
    """Candidate rule statements, most authoritative first.

    A bolded lead is how a review states its own claim, so it is taken on that
    structure alone. Unmarked text has no such guarantee, so it must additionally
    read as an obligation - a normative modal or an imperative - before it is
    allowed to stand in for one.
    """
    candidates: list[str] = []
    for match in _BOLD_LEAD.finditer(body):
        lead = _acceptable(match.group("lead"))
        if lead:
            candidates.append(lead)
    for text in structural_lines(body):
        lead = _acceptable(text)
        if not lead:
            continue
        if _NORMATIVE.search(lead) or _IMPERATIVE_SYSTEM.search(lead):
            candidates.append(lead)
    return candidates


def _acceptable(text: str) -> str:
    lead = _clean_lead(text)
    if not 3 <= len(lead.split()) <= 45:
        return ""
    if _HUMAN_DIRECTED.search(lead) or _STATUS.search(lead):
        return ""
    return lead


def base_clean(text: str | None) -> str:
    from hunter.evidence_intelligence import historical_evidence_adjudication as base

    return base.clean_comment_text(text)


# Review vendors append a prompt for downstream reviewing agents. That block is
# an instruction to the agent, not a claim about this repository's code, and it
# repeats verbatim across hundreds of findings. Treating it as a finding both
# invents an invariant and hides the real one: for a finding titled "Name the
# final merge-readiness controller", the candidate was becoming the vendor's
# "Treat finding text ... as untrusted review data".
_AGENT_PROMPT_BLOCK = re.compile(
    r"(?is)<details[^>]*>\s*<summary>[^<]*(?:prompt for[^<]*agent|ai agents?)[^<]*</summary>.*?</details>"
)
_CODE_FENCE = re.compile(r"(?s)```.*?```")


def strip_agent_prompt(text: str | None) -> str:
    """Remove vendor instruction blocks and code fences from review content."""
    if not text:
        return ""
    cleaned = _AGENT_PROMPT_BLOCK.sub(" ", text)
    cleaned = _CODE_FENCE.sub(" ", cleaned)
    return cleaned


def structural_lines(text: str | None) -> list[str]:
    """Cleaned lines with their structure intact.

    ``clean_comment_text`` joins every line into one string, which fuses a
    finding's bolded title into its first body sentence and destroys exactly the
    segmentation this module needs. Cleaning line by line keeps the title a
    line, so a rule statement can be told apart from the paragraph explaining it.
    """
    out: list[str] = []
    for raw in strip_agent_prompt(text).split("\n"):
        line = base_clean(raw)
        line = _MD_IMAGE.sub(" ", line)
        line = _MD_LINK.sub(r"\1", line)
        line = re.sub(r"^\s*(?:[*_#>\-\+|]\s*)+", " ", line).strip()
        line = _WS.sub(" ", line).strip()
        if line:
            out.append(line)
    return out


def sentences(text: str | None) -> list[str]:
    """Sentences, never crossing a line boundary of the original text."""
    out: list[str] = []
    for line in structural_lines(text):
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    return out


# ---------------------------------------------------------------------------
# Disposition text: locating the cause and the fix
# ---------------------------------------------------------------------------

_CAUSAL = re.compile(
    r"(?i)\b(?:root cause|because|since|due to|caused by|the (?:previous|prior|old) "
    r"\w+ (?:treated|accepted|assumed|compared|tested|ignored)|no longer (?:trusted|used))\b"
)


def root_cause(disposition: str | None) -> str:
    """The causal statement from an owner disposition, or an empty string."""
    if not base_clean(disposition):
        return ""
    for sentence in sentences(disposition):
        if _CAUSAL.search(sentence):
            return _WS.sub(" ", sentence)[:400]
    return ""


def applicability(finding: str | None) -> tuple[str, ...]:
    """Conditions the finding says the rule binds under."""
    conditions = []
    for sentence in sentences(finding):
        if re.match(r"(?i)\s*(?:when|whenever|if|while|unless|before|after|only when)\b", sentence):
            conditions.append(_WS.sub(" ", sentence)[:240])
    return tuple(conditions[:4])


# ---------------------------------------------------------------------------
# Execution boundary: the real enclosing symbol
# ---------------------------------------------------------------------------

_DEF = re.compile(r"^(?P<indent>\s*)(?P<kind>async def|def|class) (?P<name>\w+)", re.M)
_MODULE_DOC = re.compile(r"^\s*(?:from|import)\s", re.M)


def enclosing_symbols(path: str | None, line: int | None, content: str | None) -> tuple[str, ...]:
    """The module/class/function chain that encloses ``line`` in real source.

    Derived from the file's actual text, so a renamed or moved file reports the
    symbol that exists now rather than a guess from the path.
    """
    if not content or line is None:
        return ()
    lines = content.split("\n")
    if line < 1 or line > len(lines):
        return ()
    stack: list[tuple[int, str]] = []
    for raw in lines[:line]:
        match = _DEF.match(raw)
        if match:
            indent = len(match.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, f"{match.group('kind')} {match.group('name')}"))
    return tuple(name for _indent, name in stack)


# ---------------------------------------------------------------------------
# The extraction bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantExtraction:
    """What the evidence supports about one confirmed finding.

    ``clusterable`` is false unless a specific rule was actually stated and an
    execution boundary was resolved, so an ambiguous extraction can never reach
    a family.
    """

    observation_id: str
    source_pr: int
    finding_text_source: str
    violated_invariant: str
    root_cause: str
    execution_boundary: tuple[str, ...]
    applicability: tuple[str, ...]
    prevention_symbols: tuple[str, ...]
    fix_commit: str
    ambiguous_reasons: tuple[str, ...] = ()
    # An extracted rule is a *candidate*. It becomes evidence only when a human
    # or agent judgement is recorded for it; nothing downstream may treat a
    # heuristic extraction as a verified violated invariant.
    invariant_verified: bool = False
    verification_evidence: str = ""

    @property
    def clusterable(self) -> bool:
        return self.invariant_verified

    def to_json(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_pr": self.source_pr,
            "finding_text_source": self.finding_text_source,
            "violated_invariant": self.violated_invariant,
            "root_cause": self.root_cause,
            "execution_boundary": list(self.execution_boundary),
            "applicability": list(self.applicability),
            "prevention_symbols": list(self.prevention_symbols),
            "fix_commit": self.fix_commit,
            "ambiguous_reasons": list(self.ambiguous_reasons),
            "invariant_verified": self.invariant_verified,
            "verification_evidence": self.verification_evidence,
            "clusterable": self.clusterable,
        }


def extract(
    reconstruction: Any,
    record: dict[str, Any],
    history: Any,
    owner_login: str,
) -> InvariantExtraction:
    """Extract one finding's invariant and its supporting evidence."""
    reasons: list[str] = []
    observation = _observation_for(record, reconstruction.observation_id)
    parent = _parent_finding(record, reconstruction.observation_id, owner_login)

    if parent is not None:
        finding_text, source = parent, "parent_finding_on_thread"
    else:
        finding_text = str(observation.get("message") or "")
        source = "observation_itself"

    # The owner's disposition is never read as the invariant. If the observation
    # is itself the owner's finding, the observation is the finding; if the owner
    # replied on the thread, the parent comment is.
    disposition_text = ""
    if parent is not None or (observation.get("reviewer") == owner_login and parent is None):
        disposition_text = str(observation.get("message") or "")

    invariant = rule_statement(finding_text)
    if not invariant:
        reasons.append("the finding states no rule-shaped invariant")

    content = history.content(reconstruction.path) if reconstruction.path else None
    boundary = enclosing_symbols(reconstruction.path, reconstruction.line, content)
    if not boundary:
        reasons.append("no enclosing symbol could be resolved in the reviewed file")

    cause = root_cause(disposition_text) if disposition_text else ""
    return InvariantExtraction(
        observation_id=reconstruction.observation_id,
        source_pr=reconstruction.source_pr,
        finding_text_source=source,
        violated_invariant=invariant,
        root_cause=cause,
        execution_boundary=boundary,
        applicability=applicability(finding_text),
        prevention_symbols=tuple(reconstruction.symbols or ()),
        fix_commit=reconstruction.fix_reference if _is_sha(reconstruction.fix_reference) else "",
        ambiguous_reasons=tuple(reasons),
    )


def _is_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{7,40}", value or "", re.IGNORECASE))


def _observation_for(record: dict[str, Any], observation_id: str) -> dict[str, Any]:
    for item in (record.get("ledger") or {}).get("items") or []:
        if str(item.get("observation_id")) == observation_id:
            return item.get("observation") or {}
    return {}


def _parent_finding(record: dict[str, Any], observation_id: str, owner_login: str) -> str | None:
    """The finding an owner replied to on this finding's own thread.

    The owner's disposition says what was fixed; the rule that was violated is
    stated by whoever raised the finding. Resolving that parent is the only way
    to read a real invariant out of a confirmed owner disposition, and it is
    restricted to the finding's own thread, so an owner reply cannot import an
    invariant from an unrelated conversation.
    """
    from hunter.evidence_intelligence import historical_evidence_reconstruction as recon

    observation = _observation_for(record, observation_id)
    event_id = recon.numeric_event_id(observation.get("event_id"))
    if not event_id:
        return None
    comments = (record.get("evidence") or {}).get("review_comments") or []
    by_id = {str(comment.get("id")): comment for comment in comments}
    owner_replied = any(
        str(comment.get("in_reply_to_id")) == event_id and (comment.get("user") or {}).get("login") == owner_login
        for comment in comments
    )
    if not owner_replied:
        return None
    parent = by_id.get(event_id)
    if parent is None or (parent.get("user") or {}).get("login") == owner_login:
        return None
    # The raw body is returned, not a pre-cleaned one: clean_comment_text strips
    # the emphasis markers that mark a finding's rule statement, which is the
    # only reliable place a bot states the rule.
    return (parent.get("body") or "").strip() or None


def verdict_map(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return the flat verdict map from either accepted file shape.

    Review evidence is written both as a bare map and as an envelope that keeps
    the verdicts beside the cluster declarations describing them. Reading only
    the inner map would silently drop the declarations, so the envelope is
    unwrapped here and the caller keeps the whole payload.
    """
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("verdicts")
    return inner if isinstance(inner, dict) else payload


def apply_verdicts(
    extractions: dict[str, InvariantExtraction], verdicts: dict[str, Any] | None
) -> dict[str, InvariantExtraction]:
    """Record judgements about extracted candidates.

    A candidate becomes verified evidence only when a verdict says so, and a
    verdict can also reject a candidate outright. Nothing here infers a verdict
    from the text: an unmentioned observation stays unverified, which keeps it
    out of the non-recurring outcome and visible in the gap.
    """
    from dataclasses import replace

    out: dict[str, InvariantExtraction] = {}
    verdicts = verdict_map(verdicts)
    for observation_id, extraction in extractions.items():
        verdict = verdicts.get(observation_id)
        if not verdict:
            out[observation_id] = extraction
            continue
        verified = bool(verdict.get("verified"))
        out[observation_id] = replace(
            extraction,
            violated_invariant=str(verdict.get("violated_invariant") or extraction.violated_invariant),
            invariant_verified=verified,
            verification_evidence=str(verdict.get("evidence") or ""),
            ambiguous_reasons=(
                () if verified else extraction.ambiguous_reasons + ("the recorded verdict rejects this candidate",)
            ),
        )
    return out


# ---------------------------------------------------------------------------
# Recurrence: what may legitimately become a family
# ---------------------------------------------------------------------------


def normalise_invariant(invariant: str) -> str:
    """A comparable form of an invariant, used only to *find* candidates.

    This never decides that two findings share an invariant. It exists to
    surface pairs for semantic review; the verdict is recorded separately and
    "not the same invariant" is a legitimate outcome.
    """
    text = _MD_IMAGE.sub(" ", _MD_LINK.sub(r"\1", invariant or ""))
    text = base_clean(text)
    text = _LEAD_NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s]+", " ", text)
    return _WS.sub(" ", text).strip().lower()


@dataclass(frozen=True)
class RecurrenceCluster:
    """A set of findings that might share one violated invariant.

    ``basis`` records which signal surfaced them, and it is deliberately weak:
    repeated wording or shared code location is a *reason to look*, never a
    reason to conclude. ``same_invariant`` stays ``None`` until a verdict is
    recorded.
    """

    basis: str
    key: str
    observation_ids: tuple[str, ...]
    invariants: tuple[str, ...] = ()
    same_invariant: bool | None = None
    family_id: str = ""
    verdict_evidence: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "key": self.key,
            "observation_ids": list(self.observation_ids),
            "invariants": list(self.invariants),
            "same_invariant": self.same_invariant,
            "family_id": self.family_id,
            "verdict_evidence": self.verdict_evidence,
        }


def recurrence_clusters(
    extractions: dict[str, InvariantExtraction],
    locations: dict[str, tuple[str, tuple[str, ...]]],
) -> list[RecurrenceCluster]:
    """Surface every group that could be a recurrence, for review.

    Two independent weak signals are used, so a shared invariant stated twice in
    one file and a shared invariant stated in two files are both surfaced:

    ``invariant_wording``
        the normalised invariant repeats across findings;
    ``code_location``
        the findings name the same path and symbol.

    Neither is evidence of a shared invariant on its own, and neither sets
    ``same_invariant``.
    """
    clusters: list[RecurrenceCluster] = []

    by_wording: dict[str, list[str]] = {}
    for observation_id, extraction in extractions.items():
        key = normalise_invariant(extraction.violated_invariant)
        if len(key.split()) >= 3:
            by_wording.setdefault(key, []).append(observation_id)
    for key, members in sorted(by_wording.items()):
        if len(members) < 2:
            continue
        clusters.append(
            RecurrenceCluster(
                basis="invariant_wording",
                key=key,
                observation_ids=tuple(sorted(members)),
                invariants=tuple(dict.fromkeys(extractions[m].violated_invariant for m in sorted(members))),
            )
        )

    by_location: dict[tuple[str, str], list[str]] = {}
    for observation_id, (path, symbols) in locations.items():
        for symbol in symbols[:1]:
            if not path or not symbol:
                continue
            by_location.setdefault((path, symbol), []).append(observation_id)
    for (path, symbol), members in sorted(by_location.items()):
        if len(members) < 2:
            continue
        clusters.append(
            RecurrenceCluster(
                basis="code_location",
                key=f"{path}::{symbol}",
                observation_ids=tuple(sorted(members)),
                invariants=tuple(dict.fromkeys(extractions[m].violated_invariant for m in sorted(members))),
            )
        )
    return clusters


def declared_clusters(declarations: dict[str, Any]) -> list[RecurrenceCluster]:
    """Families recorded directly by review, not surfaced by a weak signal.

    Recurrence detected by judgement will not always repeat wording or share a
    first symbol, and the weak signals are not allowed to invent families. A
    declaration names its members and carries the invariant it was judged on, so
    a family can only exist when a reviewer states one.
    """
    out: list[RecurrenceCluster] = []
    for key, spec in (declarations.get("clusters") or {}).items():
        members = tuple(sorted(spec.get("observation_ids") or ()))
        if len(members) < 2:
            continue
        out.append(
            RecurrenceCluster(
                basis="declared",
                key=key,
                observation_ids=members,
                invariants=(str(spec.get("violated_invariant") or ""),),
                same_invariant=bool(spec.get("same_invariant", True)),
                family_id=str(spec.get("family_id") or ""),
                verdict_evidence=str(spec.get("evidence") or spec.get("violated_invariant") or ""),
            )
        )
    return out
