"""Exact-head-bound pre-ready hostile review state.

Issue #412. Before PR #411 the first complete adversarial pass over a candidate
happened *after* it was marked Ready, so predictable defect families were
discovered by an automated reviewer in a loop of Ready -> findings -> fix ->
Ready. This module makes the hostile review a piece of repository-owned state
that exists before Ready and is bound to the exact candidate content.

The binding problem and its solution
------------------------------------
A review cannot name the commit SHA it reviews, because the review is committed
*into* that commit. So the review binds **content**, exactly as the connector
authorization receipt does: it records the canonical base->HEAD change set --
operation, path, previous path and resulting git blob SHA per file -- and
excludes only its own artifact path. Any later content mutation changes some
blob SHA, and the review no longer describes the candidate: it is stale.
Re-recording the review is therefore the only way to make it current again, and
re-recording is what forces a fresh adversarial pass.

The same claim set is verified two ways from one implementation: locally against
``git diff --raw``, and by the trusted controller against GitHub's changed-file
listing for the exact head. Neither trusts the review's own account of what
changed.

Ready is blocked, not merely warned, when the review is absent, stale,
incomplete against the applicable recurring-defect families, or still carries an
unresolved blocking finding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hunter_connector_write_ingress as ingress
from hunter_workflow_state import path_matches_scope_entry

ROOT = Path(__file__).resolve().parents[1]
REVIEW_RELATIVE_PATH = ".hunter/pre-ready-hostile-review.json"
REVIEW_PATH = ROOT / REVIEW_RELATIVE_PATH
REGISTRY_RELATIVE_PATH = "docs/DEFECT_REGISTRY.json"
REGISTRY_PATH = ROOT / REGISTRY_RELATIVE_PATH
REVIEW_SCHEMA = "hunter.pre-ready-hostile-review.v1"

#: Artifacts excluded from the reviewed change set. The review cannot bind its
#: own bytes without being unsatisfiable, and the connector receipt is by
#: contract the *last* mutation, so binding it would make every valid connector
#: candidate look stale.
EXCLUDED_PATHS = frozenset({REVIEW_RELATIVE_PATH, ingress.AUTHORIZATION_RECEIPT_PATH})

CRITERION_VERDICTS = frozenset({"satisfied", "not-applicable"})
FAMILY_OUTCOMES = frozenset({"clear", "repaired"})
FINDING_SEVERITIES = frozenset({"blocking", "non-blocking"})
FINDING_RESOLUTIONS = frozenset({"resolved", "unresolved"})

#: The adversarial dimensions Issue #412 requires a large or high-risk candidate
#: to be swept in one batch rather than discovered one review round at a time.
REQUIRED_ADVERSARIAL_DIMENSIONS = (
    "authorization",
    "replay",
    "cutoff-history",
    "persistence-restrictions",
    "transaction-snapshot-consistency",
    "tamper-resistance",
    "caller-controlled-time-state",
    "monotonicity",
    "malformed-unknown-authority",
    "restart-crash-retry",
    "fail-closed",
)


#: A markdown heading whose text names the governing Issue's acceptance criteria.
_HEADING = re.compile(r"\A(#{1,6})\s+(.*?)\s*#*\s*\Z")
#: A top-level markdown list item. Indented items are sub-detail of a criterion,
#: not criteria in their own right.
_TOP_LEVEL_BULLET = re.compile(r"\A[-*+]\s+(.*)\Z")
ACCEPTANCE_CRITERIA_HEADING = "acceptance criteria"


def normalize_criterion(text: str) -> str:
    """Fold one criterion to its comparison form.

    Markdown emphasis, backticks, checkbox markers, surrounding whitespace and a
    trailing full stop all carry no meaning for *which* criterion this is, so
    they are removed before comparison. What remains is the criterion's words, so
    a review that restates a criterion in the Issue's own terms matches and one
    that names a different criterion does not.
    """

    folded = unicodedata.normalize("NFKC", text).strip()
    folded = re.sub(r"\A\[[ xX]\]\s*", "", folded)
    folded = folded.replace("`", "").replace("**", "").replace("__", "")
    folded = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", folded)
    folded = " ".join(folded.split()).casefold()
    return folded.rstrip(".").strip()


def parse_issue_acceptance_criteria(body: str) -> tuple[str, ...]:
    """The governing Issue's acceptance criteria, normalized, in order.

    Scoped to the section a heading names "acceptance criteria" so the Issue's
    other bullet lists -- required behaviour, regression scenarios -- are not
    mistaken for criteria. An Issue with no such section defines no criteria and
    imposes no coverage requirement; that is the truthful reading, not a bypass,
    because there is nothing for a review to cover.
    """

    criteria: list[str] = []
    in_section = False
    pending: str | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            normalized = normalize_criterion(pending)
            if normalized and normalized not in criteria:
                criteria.append(normalized)
            pending = None

    for raw in body.splitlines():
        heading = _HEADING.match(raw.strip())
        if heading is not None:
            flush()
            in_section = normalize_criterion(heading.group(2)) == ACCEPTANCE_CRITERIA_HEADING
            continue
        if not in_section:
            continue
        bullet = _TOP_LEVEL_BULLET.match(raw.rstrip())
        if bullet is not None:
            flush()
            pending = bullet.group(1)
        elif pending is not None and raw.strip():
            if _TOP_LEVEL_BULLET.match(raw.strip()):
                # An indented bullet is sub-detail of the criterion above it, not
                # part of that criterion's text and not a criterion of its own.
                continue
            # A wrapped continuation line belongs to the criterion above it.
            pending = f"{pending} {raw.strip()}"
        elif not raw.strip():
            flush()
    flush()
    return tuple(criteria)


@dataclass(frozen=True)
class ReviewVerdict:
    """The outcome of evaluating pre-ready review state against exact content."""

    state: str  # "valid" | "missing" | "stale" | "incomplete" | "unresolved"
    reason: str

    @property
    def ok(self) -> bool:
        return self.state == "valid"


def _canonical(claims: dict[str, Any]) -> str:
    return json.dumps(claims, sort_keys=True, separators=(",", ":"))


def review_id(claims: dict[str, Any]) -> str:
    """SHA-256 over exactly the review claims, so a hand-edited review is detectable."""

    return hashlib.sha256(_canonical(claims).encode("utf-8")).hexdigest()


def target_changes(changes: tuple[ingress.ConnectorFileChange, ...]) -> tuple[ingress.ConnectorFileChange, ...]:
    """The reviewed change set: canonical, artifact-free, rename-representation free.

    Two evidence sources describe the same candidate -- local ``git diff --raw``
    and GitHub's changed-file listing -- and they do not have to agree on whether
    a given pair of paths is a rename or an unrelated delete plus add. Rename
    detection is a heuristic with its own thresholds on each side, so binding the
    review to a representation that depends on it would make a locally valid
    review look stale to the trusted controller for no reason at all.

    So a rename is expanded into the two facts both sources always agree on: the
    old path is gone and the new path holds this content. The reviewed subject is
    unchanged -- the same paths and the same resulting bytes -- and the binding
    stops depending on a heuristic.
    """

    expanded: list[ingress.ConnectorFileChange] = []
    for change in changes:
        if change.status == "renamed":
            expanded.append(ingress.ConnectorFileChange("removed", change.previous_path, "", ""))
            expanded.append(ingress.ConnectorFileChange("added", change.path, "", change.blob_sha))
        else:
            expanded.append(change)
    # Exclusion is applied to the expanded facts, never to the original record.
    # Dropping a whole rename because its *destination* is an excluded artifact
    # would carry the disappearance of its source out of the reviewed set with
    # it, so renaming a protected file onto an artifact path would delete it
    # unreviewed. Expanded, the removal of the source survives the exclusion.
    return tuple(sorted(change for change in expanded if change.path not in EXCLUDED_PATHS))


def target_digest(changes: tuple[ingress.ConnectorFileChange, ...]) -> str:
    """A stable digest of the exact reviewed content, independent of ordering."""

    documents = [change.document() for change in target_changes(changes)]
    return hashlib.sha256(json.dumps(documents, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --- Applicable recurring-defect families -----------------------------------


def load_families(path: Path | None = None) -> tuple[tuple[dict[str, Any], ...], str]:
    """Read the recurring-defect family catalog. An unreadable catalog fails closed."""

    target = path or REGISTRY_PATH
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), f"{REGISTRY_RELATIVE_PATH} is missing"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (), f"{REGISTRY_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})"
    families = document.get("families")
    if not isinstance(families, list) or not families:
        return (), f"{REGISTRY_RELATIVE_PATH} declares no recurring-defect families"
    if not all(isinstance(entry, dict) and isinstance(entry.get("id"), str) for entry in families):
        return (), f"{REGISTRY_RELATIVE_PATH} families must be objects carrying a string id"
    return tuple(families), ""


def applicable_family_ids(families: tuple[dict[str, Any], ...], changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Family ids whose declared applicability covers at least one changed path.

    Applicability is a structural property of what the candidate touches, not a
    judgement the candidate makes about itself, so it is re-derived here from the
    trusted change set every time rather than read from the review.
    """

    applicable: list[str] = []
    for family in families:
        scope = family.get("applicability")
        entries = scope.get("changed_paths") if isinstance(scope, dict) else None
        if not isinstance(entries, list):
            continue
        if any(
            path_matches_scope_entry(path, str(entry))
            for path in changed_paths
            for entry in entries
            if isinstance(entry, str) and entry.strip()
        ):
            applicable.append(str(family["id"]))
    return tuple(sorted(applicable))


# --- Claim construction and verification ------------------------------------


def build_claims(
    *,
    issue: str,
    base_ref: str,
    base_sha: str,
    changes: tuple[ingress.ConnectorFileChange, ...],
    acceptance_criteria: tuple[dict[str, Any], ...],
    defect_families: tuple[dict[str, Any], ...],
    findings: tuple[dict[str, Any], ...],
    adversarial_dimensions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "acceptance_criteria": [dict(sorted(item.items())) for item in acceptance_criteria],
        "adversarial_dimensions": sorted(set(adversarial_dimensions)),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "defect_families": [dict(sorted(item.items())) for item in defect_families],
        "findings": [dict(sorted(item.items())) for item in findings],
        "issue": issue,
        "review_target": [change.document() for change in target_changes(changes)],
        "review_target_digest": target_digest(changes),
    }


def document_for(claims: dict[str, Any]) -> dict[str, Any]:
    return {"schema": REVIEW_SCHEMA, "claims": claims, "review_id": review_id(claims)}


def _structural_error(claims: dict[str, Any]) -> str | None:
    """Validate the review's own shape before comparing it to anything."""

    expected = {
        "acceptance_criteria",
        "adversarial_dimensions",
        "base_ref",
        "base_sha",
        "defect_families",
        "findings",
        "issue",
        "review_target",
        "review_target_digest",
    }
    if set(claims) != expected:
        return "review claims must carry exactly the canonical claim set"
    for name in ("base_ref", "base_sha", "issue", "review_target_digest"):
        if not isinstance(claims.get(name), str) or not claims[name].strip():
            return f"review claim {name!r} must be a non-empty string"
    if not str(claims["issue"]).strip().lstrip("#").isdigit():
        return "review claim 'issue' must name the governing Issue number"

    criteria = claims.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        return "review must record the governing Issue acceptance criteria"
    for item in criteria:
        if not isinstance(item, dict):
            return "each acceptance criterion must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "criterion", "evidence")):
            return "each acceptance criterion must carry a non-empty id, criterion and evidence"
        if item.get("verdict") not in CRITERION_VERDICTS:
            return f"acceptance criterion {item.get('id')!r} must be verdicted {sorted(CRITERION_VERDICTS)}"
    if not any(item.get("verdict") == "satisfied" for item in criteria):
        return "a review in which no acceptance criterion is satisfied is not a completed review"

    families = claims.get("defect_families")
    if not isinstance(families, list):
        return "review defect_families must be a list"
    for item in families:
        if not isinstance(item, dict):
            return "each defect family record must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("family", "evidence")):
            return "each defect family record must carry a non-empty family and evidence"
        if item.get("outcome") not in FAMILY_OUTCOMES:
            return f"defect family {item.get('family')!r} must record an outcome in {sorted(FAMILY_OUTCOMES)}"

    findings = claims.get("findings")
    if not isinstance(findings, list):
        return "review findings must be a list"
    for item in findings:
        if not isinstance(item, dict):
            return "each finding must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "evidence")):
            return "each finding must carry a non-empty id and evidence"
        if item.get("severity") not in FINDING_SEVERITIES:
            return f"finding {item.get('id')!r} must declare a severity in {sorted(FINDING_SEVERITIES)}"
        if item.get("resolution") not in FINDING_RESOLUTIONS:
            return f"finding {item.get('id')!r} must declare a resolution in {sorted(FINDING_RESOLUTIONS)}"

    dimensions = claims.get("adversarial_dimensions")
    if not isinstance(dimensions, list) or not all(isinstance(item, str) for item in dimensions):
        return "review adversarial_dimensions must be an array of dimension names"
    missing = sorted(set(REQUIRED_ADVERSARIAL_DIMENSIONS) - set(dimensions))
    if missing:
        return "the single adversarial batch is incomplete; missing dimensions: " + ", ".join(missing)

    if ingress.normalize_changes(claims.get("review_target")) is None:
        return "review_target must be canonical exact file transitions"
    return None


def verify_claims(
    document: Any,
    *,
    base_sha: str,
    changes: tuple[ingress.ConnectorFileChange, ...],
    families: tuple[dict[str, Any], ...],
    issue_criteria: tuple[str, ...] | None = None,
) -> ReviewVerdict:
    """Compare repository-owned review state against the exact candidate content.

    ``changes`` and ``base_sha`` must come from trusted evidence -- the local git
    range, or GitHub's changed-file listing for the exact head -- never from the
    review document, which is the thing being checked.

    ``issue_criteria`` is the governing Issue's acceptance criteria, normalized and
    derived by the caller from trusted evidence. When supplied, every one of them
    must be addressed by the review: a review is otherwise free to declare a
    single self-authored criterion satisfied and call itself complete, which
    proves nothing about the Issue it claims to have been reviewed against.
    Extra entries beyond the derived set stay allowed -- a reviewer may record
    more than the Issue asks -- because coverage is measured against the trusted
    set, never against what the review lists.

    The binding is deliberately to content rather than to a pull-request number:
    a review is a statement about a diff, so two candidates with a byte-identical
    diff from the same fork point have been reviewed by the same pass, and the
    only way to reuse a review is to reproduce exactly the content it covers.
    Every mutation, however small, changes a blob SHA and invalidates it.
    """

    if document is None:
        return ReviewVerdict("missing", "no pre-ready hostile review exists for this candidate")
    if not isinstance(document, dict) or document.get("schema") != REVIEW_SCHEMA:
        return ReviewVerdict("missing", f"pre-ready hostile review must use schema {REVIEW_SCHEMA}")
    claims = document.get("claims")
    if not isinstance(claims, dict):
        return ReviewVerdict("missing", "pre-ready hostile review claims must be an object")
    if document.get("review_id") != review_id(claims):
        return ReviewVerdict("stale", "pre-ready hostile review identifier does not match its own claims")

    problem = _structural_error(claims)
    if problem is not None:
        return ReviewVerdict("incomplete", problem)

    expected_target = [change.document() for change in target_changes(changes)]
    if claims["review_target"] != expected_target:
        return ReviewVerdict(
            "stale",
            "the pre-ready hostile review describes different content than this candidate head; "
            "the candidate was mutated after it was reviewed",
        )
    if claims["review_target_digest"] != target_digest(changes):
        return ReviewVerdict("stale", "the pre-ready hostile review digest does not bind this candidate's content")
    if claims["base_sha"].strip().lower() != base_sha.strip().lower():
        return ReviewVerdict(
            "stale",
            f"the pre-ready hostile review was taken against base {claims['base_sha'][:10]}, "
            f"not this candidate's base {base_sha[:10]}",
        )

    changed_paths = tuple(sorted({path for change in target_changes(changes) for path in change.affected_paths()}))
    required = set(applicable_family_ids(families, changed_paths))
    reviewed = {str(item.get("family")) for item in claims["defect_families"]}
    # A family this catalog does not know is deliberately not an error. The
    # trusted controller reads the catalog from the default branch, so the very
    # candidate that *adds* a family would otherwise be blocked for reviewing the
    # family it introduces. Naming an extra family cannot make an incomplete
    # review look complete either: what is required is derived from the trusted
    # catalog and the trusted changed paths, never from what the review lists.
    missing = sorted(required - reviewed)
    if missing:
        return ReviewVerdict(
            "incomplete",
            "applicable recurring-defect prevention checks are incomplete: " + ", ".join(missing),
        )

    if issue_criteria is not None:
        reviewed_criteria = {normalize_criterion(str(item.get("criterion"))) for item in claims["acceptance_criteria"]}
        uncovered = [criterion for criterion in issue_criteria if criterion not in reviewed_criteria]
        if uncovered:
            preview = "; ".join(criterion[:70] for criterion in uncovered[:3])
            return ReviewVerdict(
                "incomplete",
                f"the review does not cover {len(uncovered)} of the {len(issue_criteria)} acceptance criteria "
                f"the governing Issue defines: {preview}",
            )

    unresolved = sorted(
        str(item.get("id"))
        for item in claims["findings"]
        if item.get("severity") == "blocking" and item.get("resolution") != "resolved"
    )
    if unresolved:
        return ReviewVerdict("unresolved", "substantive review findings remain unresolved: " + ", ".join(unresolved))

    return ReviewVerdict(
        "valid",
        f"complete base->HEAD hostile review for Issue #{claims['issue']} covers "
        f"{len(required)} applicable recurring-defect famil{'y' if len(required) == 1 else 'ies'} "
        f"and {len(claims['acceptance_criteria'])} acceptance criteria"
        + (f", including all {len(issue_criteria)} the Issue defines" if issue_criteria else ""),
    )


# --- Local git evidence -----------------------------------------------------


#: GitHub's changed-file vocabulary is wider than the canonical one. Mapping is
#: explicit and total: a status not named here is unrecognised evidence and fails
#: closed rather than being silently dropped from the reviewed set.
GITHUB_STATUS_MAP = {
    "added": "added",
    "modified": "modified",
    "removed": "removed",
    "renamed": "renamed",
    "changed": "modified",
    "copied": "added",
}


def canonical_status(status: str) -> str | None:
    """The canonical change status for one GitHub status, or ``None`` if unknown."""

    return GITHUB_STATUS_MAP.get(status.strip().lower())


class GitEvidenceUnavailable(RuntimeError):
    """The local change set could not be derived, so the review cannot be checked."""


def _run_git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=False,
        capture_output=True,
        text=True,
        cwd=None if cwd is None else str(cwd),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise GitEvidenceUnavailable(detail)
    return completed.stdout


def parse_raw_diff(raw: str) -> tuple[ingress.ConnectorFileChange, ...]:
    """Parse ``git diff --raw -z -M`` into the canonical change vocabulary.

    Rename detection is on and copy detection is off, so every record maps onto
    exactly one of added/modified/removed/renamed -- the same vocabulary GitHub
    reports and the connector receipt already binds. An unrecognised record is an
    unreadable change set, not an ignorable one.
    """

    fields = [item for item in raw.split("\0")]
    changes: list[ingress.ConnectorFileChange] = []
    index = 0
    while index < len(fields):
        meta = fields[index]
        if not meta.strip():
            index += 1
            continue
        if not meta.startswith(":"):
            raise GitEvidenceUnavailable(f"unrecognised raw diff record: {meta!r}")
        parts = meta.split()
        if len(parts) != 5:
            raise GitEvidenceUnavailable(f"unrecognised raw diff record: {meta!r}")
        destination_blob = parts[3].lower()
        status = parts[4]
        code = status[0]
        if code in {"A", "M", "T"}:
            path = fields[index + 1]
            changes.append(
                ingress.ConnectorFileChange("added" if code == "A" else "modified", path, "", destination_blob)
            )
            index += 2
        elif code == "D":
            path = fields[index + 1]
            changes.append(ingress.ConnectorFileChange("removed", path, "", ""))
            index += 2
        elif code == "R":
            previous_path = fields[index + 1]
            path = fields[index + 2]
            changes.append(ingress.ConnectorFileChange("renamed", path, previous_path, destination_blob))
            index += 3
        else:
            raise GitEvidenceUnavailable(f"unsupported raw diff status {status!r}")
    normalized = ingress.normalize_changes(tuple(changes))
    if normalized is None:
        raise GitEvidenceUnavailable("the local change set is ambiguous or malformed")
    return normalized


def local_changes(base: str, head: str, *, cwd: Path | None = None) -> tuple[ingress.ConnectorFileChange, ...]:
    """The canonical change set of ``base..head`` from local git evidence."""

    # --abbrev=40 is mandatory, not cosmetic: `git diff --raw` abbreviates blob
    # SHAs by default, and an abbreviated digest cannot be compared with the full
    # blob SHA GitHub reports for the same file.
    raw = _run_git("diff", "--raw", "-z", "-M", "--abbrev=40", "--no-color", base, head, cwd=cwd)
    return parse_raw_diff(raw)


def read_review_document(path: Path | None = None) -> Any:
    target = path or REVIEW_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitEvidenceUnavailable(f"{REVIEW_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})") from exc


def verify_local(base: str, head: str = "HEAD", *, cwd: Path | None = None) -> ReviewVerdict:
    families, error = load_families()
    if error:
        return ReviewVerdict("incomplete", error)
    try:
        changes = local_changes(base, head, cwd=cwd)
        document = read_review_document()
    except GitEvidenceUnavailable as exc:
        return ReviewVerdict("incomplete", f"pre-ready hostile review evidence is unavailable ({exc})")
    return verify_claims(document, base_sha=base, changes=changes, families=families)


JUDGEMENT_KEYS = ("acceptance_criteria", "adversarial_dimensions", "defect_families", "findings")


def record(
    *,
    issue: str,
    base: str,
    head: str,
    base_ref: str,
    judgement: dict[str, Any],
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Mint review state for the exact current content of ``base..head``.

    The reviewer supplies only the judgement -- criteria verdicts, family
    outcomes, findings, and the adversarial dimensions swept. Everything that
    binds the review to the candidate is derived here from git, so a reviewer
    cannot record a review of content that does not exist.
    """

    missing = [key for key in JUDGEMENT_KEYS if key not in judgement]
    if missing:
        raise ValueError("review judgement is missing " + ", ".join(missing))
    changes = local_changes(base, head, cwd=cwd)
    claims = build_claims(
        issue=issue,
        base_ref=base_ref,
        base_sha=base,
        changes=changes,
        acceptance_criteria=tuple(judgement["acceptance_criteria"]),
        defect_families=tuple(judgement["defect_families"]),
        findings=tuple(judgement["findings"]),
        adversarial_dimensions=tuple(judgement["adversarial_dimensions"]),
    )
    return document_for(claims)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record or verify exact-head-bound pre-ready hostile review state for the governed candidate range."
        )
    )
    parser.add_argument("--base", required=True, help="Exact base (fork point) SHA of the governed candidate range.")
    parser.add_argument("--head", default="HEAD", help="Candidate head revision (default: HEAD).")
    parser.add_argument("--base-ref", default="main", help="Trusted base branch name (default: main).")
    parser.add_argument("--record", metavar="JUDGEMENT", help="Mint review state from this judgement JSON file.")
    parser.add_argument("--issue", help="Governing Issue number; required with --record.")
    args = parser.parse_args()

    if args.record:
        if not args.issue:
            print("[Pre-Ready Hostile Review] FAIL: --record requires --issue", file=sys.stderr)
            return 2
        try:
            judgement = json.loads(Path(args.record).read_text(encoding="utf-8"))
            if not isinstance(judgement, dict):
                raise ValueError("review judgement must be a JSON object")
            document = record(
                issue=str(args.issue),
                base=args.base,
                head=args.head,
                base_ref=args.base_ref,
                judgement=judgement,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, GitEvidenceUnavailable) as exc:
            print(f"[Pre-Ready Hostile Review] FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[Pre-Ready Hostile Review] RECORDED: {REVIEW_RELATIVE_PATH} ({document['review_id'][:12]})")

    verdict = verify_local(args.base, args.head)
    if not verdict.ok:
        print(f"[Pre-Ready Hostile Review] {verdict.state.upper()}: {verdict.reason}", file=sys.stderr)
        return 1
    print(f"[Pre-Ready Hostile Review] PASS: {verdict.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
