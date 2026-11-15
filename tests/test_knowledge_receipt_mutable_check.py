from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import hunter_merge_readiness_v2 as readiness


class Governance:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.posts = 0
        self.patches = 0

    def request_json(self, _repo: str, _token: str, method: str, path: str, body: dict | None = None) -> Any:
        if method == "GET":
            return {"check_runs": self.checks}
        assert body is not None
        if method == "POST":
            self.posts += 1
            self.checks.append({**body, "id": 42, "app": {"slug": "github-actions"}})
        elif method == "PATCH":
            self.patches += 1
            assert path == "check-runs/42"
            self.checks[0].update(body)
        return {}


class Collector:
    def __init__(self, governance: Governance) -> None:
        self.governance = governance


def test_one_check_survives_1001_polls_and_changed_verdict() -> None:
    governance = Governance()
    for _ in range(1001):
        lifecycle._publish_knowledge_receipt_check(
            Collector(governance),
            "fafa33/Project-Hunter",
            "token",
            "a" * 40,
            747,
            "success",
            "no obligation",
            obligation_fingerprint="a" * 16,
        )
    assert governance.posts == 1
    assert governance.patches == 1000
    assert len(governance.checks) == 1
    lifecycle._publish_knowledge_receipt_check(
        Collector(governance),
        "fafa33/Project-Hunter",
        "token",
        "a" * 40,
        747,
        "failure",
        "finding unresolved",
        obligation_fingerprint="b" * 16,
    )
    assert governance.posts == 1
    assert governance.checks[0]["conclusion"] == "failure"


def test_stale_foreign_and_wrong_head_checks_fail_closed() -> None:
    now = datetime.now(UTC)
    check = {
        "name": "Hunter Knowledge Receipt / PR #747",
        "external_id": "knowledge-receipt:v1:747:" + "a" * 40,
        "head_sha": "a" * 40,
        "app": {"slug": "github-actions"},
        "status": "completed",
        "conclusion": "success",
        "completed_at": now.isoformat(),
        "output": {"summary": "no obligation [rt:" + "a" * 16 + "]"},
    }
    assert readiness.knowledge_receipt_verdict_from_checks([check], 747, "a" * 40)[0] == "success"
    assert readiness.knowledge_receipt_verdict_from_checks([check], 747, "b" * 40)[0] == "pending"
    assert (
        readiness.knowledge_receipt_verdict_from_checks([{**check, "app": {"slug": "foreign"}}], 747, "a" * 40)[0]
        == "pending"
    )
    assert (
        readiness.knowledge_receipt_verdict_from_checks(
            [{**check, "completed_at": (now - timedelta(hours=2)).isoformat()}], 747, "a" * 40
        )[0]
        == "pending"
    )
