"""Deterministic historical evidence adjudication over a frozen full-history scan.

The full-history defect scan proves *scan* coverage: every in-scope PR carries a
terminal scan status. It does not prove *evidence* coverage. An observation the
scanner could not label lands in the learning ledger as ``insufficient-evidence``
and stays there, because labeling it is a judgment the scanner is not entitled to
make: a third-party reviewer's assertion is evidence, not authority.

This module supplies the missing phase. It re-adjudicates the ledger items the
scan already captured, in place, over the same frozen snapshot and the same
persisted records -- it never re-fetches the PR walk and never discards it.

Closure categories (exactly one per ledger item):

``A``
    Confirmed defect. The repository's own authority asserted it: either the
    owner authored the finding, or the owner disposed of the finding on its
    review thread.
``B``
    Confirmed and deterministically mapped onto an existing canonical family,
    by exact canonical-invariant equality plus canonical applicability
    intersection.
``C``
    Excluded with auditable evidence: not an individual finding, no substantive
    content, or explicitly dispositioned non-defect / false-positive / style /
    obsolete / infrastructure / provider-unavailable.
``AMBIGUOUS``
    A substantive third-party claim the repository never adjudicated. These are
    never auto-confirmed (a third-party label is not authority) and never
    auto-excluded (they are substantive claims, not noise), so they are queued
    for an explicit owner decision.

Every decision records the rule that produced it and the evidence it read, so
the whole phase is replayable and auditable. Nothing here writes to the
canonical registry or backfill: adjudication produces proposals, and only an
owner decision can make a proposal canonical.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.full_history_defect_scan import (
    atomic_write_json,
    read_json_file,
    stable_json_digest,
)

ADJUDICATION_SCHEMA_VERSION = "hunter-historical-evidence-adjudication-v1"
CLOSURE_SCHEMA_VERSION = "hunter-historical-closure-manifest-v1"
ADJUDICATION_PROGRESS_SCHEMA_VERSION = "hunter-historical-adjudication-progress-v1"

# Closure dispositions.
DISPOSITION_CONFIRMED = "A-confirmed"
DISPOSITION_EXISTING_FAMILY = "B-existing-family"
DISPOSITION_EXCLUDED = "C-excluded"
DISPOSITION_AMBIGUOUS = "AMBIGUOUS"

# Rule identifiers. Recorded on every decision so the phase is auditable.
RULE_PRESERVE_TERMINAL = "R0-preserve-proven-terminal-state"
RULE_NO_LOCATION_ANCHOR = "R1-no-location-anchor"
RULE_OWNER_ASSERTED = "R2-owner-asserted-finding"
RULE_OWNER_DISPOSED_THREAD = "R3-owner-disposed-review-thread"
RULE_NO_SUBSTANTIVE_CONTENT = "R4-no-substantive-content"
RULE_UNADJUDICATED_THIRD_PARTY = "R5-unadjudicated-third-party-claim"

EXCLUSION_CLASSIFICATIONS = ("false-positive", "style", "obsolete", "infrastructure", "provider-unavailable")

# Ledger states the scan already resolved against canonical evidence. These are
# carried forward verbatim: re-deriving them would discard proven canonical
# mappings (an HBF-preloaded observation deterministically matched to a family)
# and replace them with a weaker re-inference.
TERMINAL_LEDGER_STATES = ("existing-family", "excluded", "candidate-new-family")

# A strict abbreviated/full commit SHA, used only to extract an auditable fix
# reference from owner prose. Never used to decide a disposition on its own.
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_HTML_SUB = re.compile(r"</?sub>")
_IMAGE_MARKDOWN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Emphasis/heading noise, but only at token boundaries: an inner underscore is
# part of a snake_case code identifier (``applicability_end``) and carries the
# invariant's meaning, so it must survive into the canonical comparison.
_EDGE_MARKUP = re.compile(r"(?:(?<=\s)|^)[*_`#>]+|[*_`#>]+(?=\s|$)")
# Markup characters that can never be part of an identifier.
_ALWAYS_NOISE = re.compile(r"[*`#>]")
# Hidden ``<details>`` blocks hold the reviewer's own tool transcript ("Analysis
# chain", "Script executed"), never the claim. Left in place they become the
# grouping signature, so 700+ owner decisions would be grouped by bot UI chrome.
_DETAILS_BLOCK = re.compile(r"<\s*details\b.*?<\s*/\s*details\s*>", re.I | re.S)
_SUMMARY_TAG = re.compile(r"<\s*/?\s*summary\b[^>]*>", re.I)
# Bot metadata trailers, e.g. "<!-- This is an auto-generated reply by CodeRabbit -->".
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# A review taxonomy banner ("Data Integrity | Minor | Quick win") is reviewer
# categorization, not a claim about the code.
_BANNER_LINE = re.compile(r"^[^|\n]{0,60}(?:\|[^|\n]{0,40}){2,}[^\n]{0,40}$")
_WHITESPACE = re.compile(r"\s+")

# Owner disposition prose -> classification. Ordered: the first pattern that
# matches at the start of the message wins, so a negation is never overridden by
# a later "fixed" mention.
_DISPOSITION_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^(?:confirmed\s+and\s+fixed|confirmed\s+as\s+a\s+real\s+gap|confirmed|valid|verified)\b[,: ]*", re.I
        ),
        "confirmed",
    ),
    (
        re.compile(
            r"^(?:false\s+positive|not\s+an\s+issue|not\s+a\s+bug|not\s+a\s+defect|incorrect|invalid\s+finding)\b[,: ]*",
            re.I,
        ),
        "false-positive",
    ),
    (re.compile(r"^(?:style|cosmetic|nit)\b[,: ]*", re.I), "style"),
    (re.compile(r"^(?:obsolete|superseded|stale|already\s+(?:fixed|addressed))\b[,: ]*", re.I), "obsolete"),
    (
        re.compile(
            r"^(?:resolved\s+in|fixed\s+in|addressed\s+in|corrected\s+in|remediated\s+in|fixed|resolved|addressed)\b\s*",
            re.I,
        ),
        "confirmed",
    ),
)

# A message that is only an approval or a thank-you carries no defect claim.
_APPROVAL_ONLY = re.compile(
    r"^(?:lgtm|looks good|no issues|nice|thanks|thank you|approved|great|perfect|ship it|ok|okay)[.! ]*$", re.I
)

_MIN_SUBSTANTIVE_CHARS = 40
# A reviewer declining to process another reviewer's comment states no claim.
_NO_CLAIM_NOTICE = re.compile(r"skipped:\s*comment is from another (?:github )?bot|comment is from another bot", re.I)


def clean_comment_text(message: str | None) -> str:
    """Strip badge/markup noise so a claim's substance can be read and grouped."""
    text = _IMAGE_MARKDOWN.sub(" ", _HTML_SUB.sub(" ", message or ""))
    text = _HTML_COMMENT.sub(" ", text)
    text = _DETAILS_BLOCK.sub(" ", text)
    text = _SUMMARY_TAG.sub(" ", text)
    text = _EDGE_MARKUP.sub(" ", text)
    text = _ALWAYS_NOISE.sub(" ", text)
    # Line structure is preserved until the taxonomy banner is dropped, because
    # the banner is only identifiable as a line.
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    while lines and _BANNER_LINE.match(lines[0]):
        lines.pop(0)
    return _WHITESPACE.sub(" ", " ".join(lines)).strip()


def normalize_invariant(text: str | None) -> str:
    """Canonical invariant normalization used for exact family matching."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def group_key(text: str | None) -> str:
    """Deterministic grouping key for owner-facing decision queues.

    Runs the same cleaning as adjudication, so reviewer UI chrome (hidden tool
    transcripts, taxonomy banners, markup) cannot become the grouping signature
    and split one real claim into many chrome-shaped groups.
    """
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", clean_comment_text(text).lower()).split())


def split_owner_disposition(message: str | None) -> tuple[str | None, str, str]:
    """Return ``(classification, substance, fix_reference)`` for owner prose.

    ``classification`` is ``None`` when the owner authored no explicit
    disposition. ``substance`` is the claim with the disposition prefix and any
    commit SHA removed, because the prefix states the outcome and the substance
    states the invariant.
    """
    text = clean_comment_text(message)
    for pattern, classification in _DISPOSITION_PREFIXES:
        match = pattern.match(text)
        if not match:
            continue
        rest = text[match.end() :]
        sha = _SHA.search(rest)
        fix_reference = sha.group(0) if sha else ""
        if fix_reference:
            rest = rest.replace(fix_reference, " ")
        rest = re.sub(r"^[\s:.,`\-–—]+", "", rest)
        return classification, rest.strip(), fix_reference
    return None, text, ""


@dataclass(frozen=True)
class Adjudication:
    """One ledger item's adjudicated disposition, with its auditable evidence."""

    source_pr: int
    observation_id: str
    event_id: str
    provider: str
    reviewer: str
    rule: str
    disposition: str
    classification: str
    invariant: str
    affected_paths: list[str]
    fix_reference: str
    claimed_family_id: str | None
    evidence: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _comment_index(record: dict[str, Any]) -> tuple[dict[str, Any], dict[int, Any]]:
    """Index the frozen evidence: comment by id, and owner reply by parent id."""
    comments = (record.get("evidence") or {}).get("review_comments") or []
    by_id: dict[str, Any] = {}
    reply_by_parent: dict[int, Any] = {}
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        by_id[str(comment.get("id"))] = comment
        parent = comment.get("in_reply_to_id")
        if parent is not None:
            reply_by_parent[int(parent)] = comment
    return by_id, reply_by_parent


def _login(comment: dict[str, Any] | None) -> str:
    if not isinstance(comment, dict):
        return ""
    return str((comment.get("user") or {}).get("login") or "")


def _family_index(registry_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    families = raw.get("families")
    if not isinstance(families, list):
        raise ValueError("canonical registry families must be a list")
    return [family for family in families if isinstance(family, dict)]


def _path_intersects(path: str, selector: str) -> bool:
    if selector.endswith("/"):
        return path.startswith(selector)
    return path == selector or path.startswith(selector + "/")


def _match_family(invariant: str, affected_paths: Iterable[str], families: list[dict[str, Any]]) -> str | None:
    """Deterministic existing-family match: exact invariant + applicability.

    Deliberately strict. A family is claimed only when the reviewer's claim
    restates the canonical invariant *verbatim* (identical after normalization)
    and the finding's paths intersect the family's canonical applicability. A
    merely similar, paraphrased, or longer-than-canonical claim is not a match;
    guessing here would fabricate canonical history.

    When the claim restates more than one canonical invariant the match is
    ambiguous, so no family is claimed and the item stays in the owner queue.
    """
    normalized = normalize_invariant(invariant)
    if not normalized:
        return None
    matches: list[str] = []
    for family in families:
        canonical = normalize_invariant(str(family.get("invariant") or ""))
        # Require a substantial canonical invariant so a short generic phrase
        # cannot match incidental wording.
        if len(canonical) < 40 or canonical not in normalized:
            continue
        selectors = (family.get("applicability") or {}).get("changed_paths") or []
        if any(_path_intersects(path, str(selector)) for path in affected_paths for selector in selectors):
            matches.append(str(family.get("id")))
    if len(matches) == 1:
        return matches[0]
    # Zero matches: no canonical family. Multiple: ambiguous, claim nothing.
    return None


def adjudicate_item(
    item: dict[str, Any],
    record: dict[str, Any],
    *,
    owner_login: str,
    families: list[dict[str, Any]],
) -> Adjudication:
    """Adjudicate one ledger item from already-captured evidence only."""
    observation = item.get("observation") or {}
    event_id = str(observation.get("event_id") or "")
    reviewer = str(observation.get("reviewer") or "")
    message = str(observation.get("message") or "")
    path = str(observation.get("path") or "")
    line = observation.get("line")
    by_id, reply_by_parent = _comment_index(record)
    comment = by_id.get(event_id.rsplit("-", 1)[-1])
    parent_id = comment.get("id") if isinstance(comment, dict) else None
    reply = reply_by_parent.get(int(parent_id)) if parent_id is not None else None
    owner_reply = reply if _login(reply) == owner_login and owner_login else None

    def build(
        rule: str,
        disposition: str,
        classification: str,
        invariant: str,
        paths: list[str],
        evidence: str,
        fix_reference: str = "",
        claimed: str | None = None,
    ) -> Adjudication:
        return Adjudication(
            source_pr=int(observation.get("source_pr") or record.get("pr_number") or 0),
            observation_id=str(item.get("observation_id") or ""),
            event_id=event_id,
            provider=str(observation.get("provider") or ""),
            reviewer=reviewer,
            rule=rule,
            disposition=disposition,
            classification=classification,
            invariant=invariant,
            affected_paths=paths,
            fix_reference=fix_reference,
            claimed_family_id=claimed,
            evidence=evidence,
        )

    # R0: the scan already resolved this item against canonical evidence.
    # Preserved verbatim; re-inferring it would discard a proven mapping.
    prior_state = str(item.get("state") or "")
    prior_classification = str(observation.get("classification") or "")
    if prior_state in TERMINAL_LEDGER_STATES and prior_classification:
        claimed = observation.get("claimed_family_id")
        if prior_state == "existing-family" and claimed:
            return build(
                RULE_PRESERVE_TERMINAL,
                DISPOSITION_EXISTING_FAMILY,
                prior_classification,
                clean_comment_text(message),
                [path] if path else [],
                f"scan already resolved this observation to canonical family {claimed}",
                "",
                str(claimed),
            )
        if prior_state == "excluded" or prior_classification in EXCLUSION_CLASSIFICATIONS:
            return build(
                RULE_PRESERVE_TERMINAL,
                DISPOSITION_EXCLUDED,
                prior_classification,
                clean_comment_text(message),
                [path] if path else [],
                f"scan already resolved this observation as {prior_classification!r}",
            )

    # R1: no file/line anchor -> not an individual finding. A defect claim names
    # the code it is about; a review summary, umbrella comment, or instruction
    # names none, so it cannot be a finding about this repository's code.
    if not path or line is None:
        return build(
            RULE_NO_LOCATION_ANCHOR,
            DISPOSITION_EXCLUDED,
            "not-an-individual-finding",
            "",
            [],
            f"observation carries no file/line anchor (path={path!r}, line={line!r}); "
            "a review container, summary, or instruction is not a defect finding",
        )

    affected = [path]
    is_owner_authored = bool(owner_login) and reviewer == owner_login

    # R2: the owner authored this finding. Owner authorship is the repository's
    # own authority asserting a defect, not a third-party label.
    if is_owner_authored:
        classification, substance, fix_reference = split_owner_disposition(message)
        if classification is None:
            classification = "confirmed"
        if classification in EXCLUSION_CLASSIFICATIONS:
            return build(
                RULE_OWNER_ASSERTED,
                DISPOSITION_EXCLUDED,
                classification,
                substance,
                affected,
                f"owner-authored disposition classified {classification!r}",
                fix_reference,
            )
        family = _match_family(substance, affected, families)
        return build(
            RULE_OWNER_ASSERTED,
            DISPOSITION_EXISTING_FAMILY if family else DISPOSITION_CONFIRMED,
            "confirmed",
            substance,
            affected,
            "owner-authored finding asserted by repository authority"
            + (f"; deterministically matched {family}" if family else "; no canonical family matched"),
            fix_reference,
            family,
        )

    # R3: a third-party finding the owner disposed of on its own review thread.
    if owner_reply is not None:
        classification, _substance, fix_reference = split_owner_disposition(str(owner_reply.get("body") or ""))
        if classification in EXCLUSION_CLASSIFICATIONS:
            return build(
                RULE_OWNER_DISPOSED_THREAD,
                DISPOSITION_EXCLUDED,
                classification,
                clean_comment_text(message),
                affected,
                f"owner disposed of the third-party finding on its thread as {classification!r}",
                fix_reference,
            )
        if classification == "confirmed":
            substance = clean_comment_text(message)
            family = _match_family(substance, affected, families)
            return build(
                RULE_OWNER_DISPOSED_THREAD,
                DISPOSITION_EXISTING_FAMILY if family else DISPOSITION_CONFIRMED,
                "confirmed",
                substance,
                affected,
                "owner confirmed the third-party finding on its review thread"
                + (f"; deterministically matched {family}" if family else "; no canonical family matched"),
                fix_reference,
                family,
            )

    # R4: no substantive content -> nothing to adjudicate. A bot declining to
    # process another bot's comment, or a bare approval, states no claim about
    # this repository's code even when it carries a file/line anchor.
    cleaned = clean_comment_text(message)
    if _NO_CLAIM_NOTICE.search(cleaned) or len(cleaned) < _MIN_SUBSTANTIVE_CHARS or _APPROVAL_ONLY.match(cleaned):
        return build(
            RULE_NO_SUBSTANTIVE_CONTENT,
            DISPOSITION_EXCLUDED,
            "no-substantive-content",
            "",
            [],
            "comment carries no substantive defect claim",
        )

    # R5: substantive third-party claim, never adjudicated by the repository.
    return build(
        RULE_UNADJUDICATED_THIRD_PARTY,
        DISPOSITION_AMBIGUOUS,
        "",
        clean_comment_text(message),
        affected,
        "substantive third-party claim with no owner authorship and no owner thread disposition; "
        "a third-party label is evidence, not authority, so it is neither confirmed nor excluded here",
    )


def iter_ledger_items(data_dir: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield ``(pr_number, record)`` for every persisted scan record."""
    prs_dir = data_dir / "prs"
    if not prs_dir.is_dir():
        return
    for path in sorted(prs_dir.glob("*.json"), key=lambda item: item.stem):
        record = read_json_file(path, required=True)
        if isinstance(record, dict):
            yield int(record.get("pr_number") or path.stem), record


def load_item_total(data_dir: Path) -> int:
    """Total ledger items the closure must reconcile to."""
    total = 0
    for _pr, record in iter_ledger_items(data_dir):
        items = (record.get("ledger") or {}).get("items")
        if isinstance(items, list):
            total += len(items)
    return total


def adjudicate_record(
    record: dict[str, Any], *, owner_login: str, families: list[dict[str, Any]]
) -> list[Adjudication]:
    """Adjudicate every ledger item of one PR record, deterministically ordered."""
    items = (record.get("ledger") or {}).get("items") or []
    return [
        adjudicate_item(item, record, owner_login=owner_login, families=families)
        for item in sorted(items, key=lambda entry: str(entry.get("observation_id") or ""))
    ]


def engine_digest() -> str:
    """Digest of the decision logic itself, not just of its inputs.

    The resume cache is keyed on this as well as on the registry digest. Without
    it, editing a rule would silently reuse decisions produced by the *previous*
    rule set and report a clean reconciliation over stale verdicts.
    """
    import inspect

    parts = [ADJUDICATION_SCHEMA_VERSION, CLOSURE_SCHEMA_VERSION]
    for obj in (adjudicate_item, _match_family, split_owner_disposition, clean_comment_text, normalize_invariant):
        parts.append(inspect.getsource(obj))
    parts.extend(
        [
            repr([(pattern.pattern, classification) for pattern, classification in _DISPOSITION_PREFIXES]),
            _APPROVAL_ONLY.pattern,
            _SHA.pattern,
            _EDGE_MARKUP.pattern,
            _ALWAYS_NOISE.pattern,
            repr(_MIN_SUBSTANTIVE_CHARS),
            repr(sorted(EXCLUSION_CLASSIFICATIONS)),
            repr(sorted(TERMINAL_LEDGER_STATES)),
        ]
    )
    return stable_json_digest(parts)


def _decision_queue(adj: list[Adjudication]) -> dict[str, Any]:
    """Group the owner queue so the owner decides per case, not per item.

    Grouped on the derived invariant's leading words plus the affected path
    root, which collapses repeats of the same case without pretending two
    differently-worded findings are the same root cause.
    """
    buckets: dict[tuple[str, str], list[Adjudication]] = {}
    for item in adj:
        if item.disposition != DISPOSITION_AMBIGUOUS:
            continue
        path_root = item.affected_paths[0].rsplit("/", 1)[0] if item.affected_paths else ""
        key = (path_root, " ".join(group_key(item.invariant).split()[:8]))
        buckets.setdefault(key, []).append(item)
    groups = []
    for (path_root, signature), items in sorted(buckets.items()):
        prs = sorted({item.source_pr for item in items})
        groups.append(
            {
                "group_signature": signature,
                "path_root": path_root,
                "item_count": len(items),
                "prs": prs,
                "example_evidence": items[0].invariant[:400],
                "proposed_decision": "confirm-and-map-to-family | exclude-as-non-defect | out-of-scope-advisory",
            }
        )
    groups.sort(key=lambda group: (-group["item_count"], group["group_signature"]))
    return {
        "ambiguous_item_count": sum(group["item_count"] for group in groups),
        "group_count": len(groups),
        "groups": groups,
    }


def build_closure_manifest(
    *,
    repository: str,
    data_dir: Path,
    adjudications: list[Adjudication],
    scan_statused: int,
    scan_total: int,
) -> dict[str, Any]:
    """Build the strict closure manifest.

    ``coverage_gap`` is zero only when *every* closure dimension is zero. Scan
    completion alone can never produce a zero ``coverage_gap``.
    """
    by_rule: dict[str, int] = {}
    by_classification: dict[str, int] = {}
    # Stable schema: every disposition category is always present, so a consumer
    # never has to distinguish "absent" from "zero".
    by_disposition: dict[str, int] = {
        DISPOSITION_CONFIRMED: 0,
        DISPOSITION_EXISTING_FAMILY: 0,
        DISPOSITION_EXCLUDED: 0,
        DISPOSITION_AMBIGUOUS: 0,
    }
    for item in adjudications:
        by_disposition[item.disposition] = by_disposition.get(item.disposition, 0) + 1
        by_rule[item.rule] = by_rule.get(item.rule, 0) + 1
        if item.classification:
            by_classification[item.classification] = by_classification.get(item.classification, 0) + 1

    ledger_total = load_item_total(data_dir)
    adjudicated = len(adjudications)
    confirmed = by_disposition[DISPOSITION_CONFIRMED]
    mapped = by_disposition[DISPOSITION_EXISTING_FAMILY]
    excluded = by_disposition[DISPOSITION_EXCLUDED]
    ambiguous = by_disposition[DISPOSITION_AMBIGUOUS]

    scan_coverage_gap = scan_total - scan_statused
    # Mechanical completeness: every ledger item must carry an adjudication.
    adjudication_coverage_gap = ledger_total - adjudicated
    # A confirmed defect with no canonical family is unmapped canonical history.
    canonical_mapping_gap = confirmed
    # Adjudicated, but landing in the owner queue: judgment still pending.
    unresolved_evidence_count = ambiguous
    coverage_gap = scan_coverage_gap + adjudication_coverage_gap + canonical_mapping_gap + unresolved_evidence_count

    manifest = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "repository": repository,
        "scan": {
            "total_prs": scan_total,
            "statused_prs": scan_statused,
            "scan_coverage_gap": scan_coverage_gap,
        },
        "reconciliation": {
            "raw_scan_ledger_items": ledger_total,
            "adjudicated_items": adjudicated,
            "confirmed": confirmed,
            "mapped_to_existing_family": mapped,
            "excluded": excluded,
            "ambiguous_items": ambiguous,
            "disposition_sum": confirmed + mapped + excluded + ambiguous,
            "reconciles_exactly": confirmed + mapped + excluded + ambiguous == ledger_total
            and adjudicated == ledger_total,
        },
        "dispositions": dict(sorted(by_disposition.items())),
        "rules": dict(sorted(by_rule.items())),
        "classifications": dict(sorted(by_classification.items())),
        "closure": {
            "confirmed": confirmed,
            "mapped_to_existing_family": mapped,
            "excluded": excluded,
            "ambiguous": ambiguous,
        },
        "scan_coverage_gap": scan_coverage_gap,
        "adjudication_coverage_gap": adjudication_coverage_gap,
        "canonical_mapping_gap": canonical_mapping_gap,
        "unresolved_evidence_count": unresolved_evidence_count,
        "coverage_gap": coverage_gap,
    }
    manifest["closure_manifest_digest"] = stable_json_digest(manifest)
    return manifest


def run_adjudication(
    *,
    data_dir: Path,
    registry_path: Path,
    owner_login: str,
    repository: str,
    resume: bool = True,
) -> dict[str, Any]:
    """Adjudicate the frozen scan in place. Resumable; never re-fetches the walk.

    Per-PR adjudication files are written under ``adjudication/`` and existing
    ones are reused on resume, so an interrupted run continues without
    discarding the scan or the decisions already made.
    """
    families = _family_index(registry_path)
    out_dir = data_dir / "adjudication"
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = data_dir / "snapshot.json"
    if not snapshot_path.is_file():
        raise SystemExit(f"no scan artifacts under {data_dir}; run the worker first")
    snapshot = read_json_file(snapshot_path, required=True)
    assert isinstance(snapshot, dict)
    scan_total = len(snapshot.get("prs") or [])

    adjudications: list[Adjudication] = []
    reused = 0
    registry_digest = stable_json_digest({"families": families})
    rules_digest = engine_digest()
    for pr_number, record in iter_ledger_items(data_dir):
        target = out_dir / f"{pr_number}.json"
        if resume and target.is_file():
            cached = read_json_file(target, required=True)
            if (
                isinstance(cached, dict)
                and cached.get("registry_digest") == registry_digest
                and cached.get("engine_digest") == rules_digest
            ):
                adjudications.extend(Adjudication(**entry) for entry in cached.get("adjudications") or [])
                reused += 1
                continue
        decided = adjudicate_record(record, owner_login=owner_login, families=families)
        atomic_write_json(
            target,
            {
                "schema_version": ADJUDICATION_SCHEMA_VERSION,
                "source_pr": pr_number,
                "registry_digest": registry_digest,
                "engine_digest": rules_digest,
                "adjudications": [item.to_json() for item in decided],
            },
        )
        adjudications.extend(decided)

    adjudications.sort(key=lambda item: (item.source_pr, item.observation_id))
    manifest = build_closure_manifest(
        repository=repository,
        data_dir=data_dir,
        adjudications=adjudications,
        scan_statused=scan_total,
        scan_total=scan_total,
    )
    atomic_write_json(data_dir / "historical_closure_manifest.json", manifest)
    atomic_write_json(
        data_dir / "adjudication" / "owner_decision_queue.json",
        {
            "schema_version": ADJUDICATION_PROGRESS_SCHEMA_VERSION,
            "reused_pr_records": reused,
            "queue": _decision_queue(adjudications),
        },
    )
    return manifest
