import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hunter_merge_readiness


def _record(*pr_numbers: int) -> dict[str, Any]:
    pulls = [{"number": number} for number in pr_numbers]
    return {"pull_requests": pulls}


def _belongs(record: dict[str, Any], pr_number: int, unique: bool) -> bool:
    return hunter_merge_readiness.evidence_belongs_to_pull_request(
        record,
        pr_number,
        lambda: unique,
    )


def test_multi_pr_status_evidence_fails_closed_on_shared_head():
    record = _record(123, 456)

    assert not _belongs(record, 123, False)
    assert not _belongs(record, 456, False)


def test_multi_pr_check_evidence_fails_closed_on_shared_head():
    record = _record(123, 456)

    assert not _belongs(record, 123, False)
    assert not _belongs(record, 456, False)


def test_singleton_association_is_authoritative_only_for_that_pr():
    record = _record(123)

    assert _belongs(record, 123, False)
    assert not _belongs(record, 456, True)


def test_ambiguous_association_uses_unique_head_fallback():
    assert _belongs(_record(), 123, True)
    assert _belongs(_record(123, 456), 123, True)
