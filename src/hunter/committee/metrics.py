from __future__ import annotations

from hunter.committee.models import CommitteeVote

APPROVAL = {"STRONG_APPROVE", "APPROVE"}
OPPOSITION = {"STRONG_OPPOSE", "OPPOSE"}
ABSTAIN = {"ABSTAIN_MISSING", "ABSTAIN_STALE", "ABSTAIN_LOW_CONFIDENCE"}


def approval_score(votes: tuple[CommitteeVote, ...]) -> float:
    return _weighted(votes, APPROVAL)


def opposition_score(votes: tuple[CommitteeVote, ...]) -> float:
    return _weighted(votes, OPPOSITION)


def consensus_score(votes: tuple[CommitteeVote, ...]) -> float:
    active = tuple(vote for vote in votes if vote.vote not in ABSTAIN)
    if not active:
        return 0.0
    approve = approval_score(active)
    oppose = opposition_score(active)
    return _clamp(max(approve, oppose) * (1.0 - min(approve, oppose)))


def conflict_score(votes: tuple[CommitteeVote, ...]) -> float:
    approve = approval_score(votes)
    oppose = opposition_score(votes)
    return _clamp(min(approve, oppose) * 2.0)


def evidence_robustness(votes: tuple[CommitteeVote, ...], missing_ratio: float, stale_ratio: float) -> float:
    active_ratio = sum(1 for vote in votes if vote.vote not in ABSTAIN) / max(1, len(votes))
    confidence = average(tuple(vote.source_confidence for vote in votes))
    return _clamp((active_ratio + confidence) / 2.0 - missing_ratio - stale_ratio)


def average(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return _clamp(sum(_clamp(value) for value in values) / len(values))


def _weighted(votes: tuple[CommitteeVote, ...], states: set[str]) -> float:
    total = sum(vote.normalized_contribution for vote in votes)
    if total <= 0.0:
        return 0.0
    return _clamp(sum(vote.normalized_contribution for vote in votes if vote.vote in states) / total)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
