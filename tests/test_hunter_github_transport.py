"""Regression tests for the shared Hunter GitHub transport resilience boundary.

Proofs covered (mission A/B requirements):

- transient HTTP failures (429/500/502/503/504) are retried with bounded
  deterministic backoff and recover when GitHub recovers;
- repeated transient failures exhaust after exactly the policy's attempt
  bound and raise the typed ``GitHubUnavailable`` fail-closed outcome;
- ``Retry-After`` is honored, capped at the policy maximum so CI stays
  bounded;
- node-resolution 404s are infrastructure failures: retried like transient
  errors, never treated as "not found";
- ordinary 404s (and every other permanent status) are never retried;
- transport errors (URLError) and malformed JSON are transient;
- ``gh`` CLI failure diagnostics are classified the same way;
- backoff delays are deterministic (no jitter, CI-safe).
"""

import io
import json
import urllib.error
import urllib.request

import hunter_github_transport as transport
import pytest


def _http_error(status: int, body: str, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/test",
        status,
        "failure",
        headers or {},
        io.BytesIO(body.encode("utf-8")),
    )


class TestClassification:
    def test_http_503_classified_transient(self):
        exc = transport.classify_http_error(_http_error(503, "No server is currently available to process the request"))
        assert exc.category == "transient"
        assert exc.status_code == 503
        assert exc.retryable

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_all_transient_statuses_retryable(self, code):
        exc = transport.classify_http_error(_http_error(code, "boom"))
        assert exc.category == "transient"
        assert exc.retryable

    def test_node_resolution_404_is_retryable_infrastructure_failure(self):
        exc = transport.classify_http_error(
            _http_error(404, "Could not resolve to a node with the global id of 'PR_kwDOTRDHr87_tH6X'")
        )
        assert exc.category == "node-resolution"
        assert exc.status_code == 404
        assert exc.retryable

    def test_ordinary_404_is_permanent_and_never_retried(self):
        exc = transport.classify_http_error(_http_error(404, "Not Found"))
        assert exc.category == "permanent"
        assert not exc.retryable

    def test_other_status_permanent(self):
        exc = transport.classify_http_error(_http_error(403, "rate limited"))
        assert exc.category == "permanent"
        assert not exc.retryable

    def test_retry_after_parsed_from_headers(self):
        exc = transport.classify_http_error(_http_error(503, "busy", {"Retry-After": "7"}))
        assert exc.retry_after == 7.0

    def test_retry_after_http_date_form_is_ignored(self):
        exc = transport.classify_http_error(_http_error(503, "busy", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
        assert exc.retry_after is None

    def test_cli_failure_transient_http_classified(self):
        exc = transport.classify_cli_failure("gh api failed: HTTP 503\nNo server is currently available")
        assert exc.category == "transient"
        assert exc.status_code == 503
        assert exc.retryable

    def test_cli_failure_node_resolution_404_classified(self):
        exc = transport.classify_cli_failure(
            "HTTP 404\nCould not resolve to a node with the global id of 'PR_kwDOTRDHr87_tH6X'"
        )
        assert exc.category == "node-resolution"
        assert exc.retryable

    def test_cli_failure_plain_404_permanent(self):
        exc = transport.classify_cli_failure("HTTP 404: Not Found")
        assert exc.category == "permanent"
        assert not exc.retryable

    def test_cli_failure_git_command_permanent(self):
        exc = transport.classify_cli_failure("fatal: not a git repository")
        assert exc.category == "permanent"
        assert not exc.retryable

    def test_urlerror_classified_transport(self):
        exc = transport.classify_transport_error(urllib.error.URLError("connection refused"))
        assert exc.category == "transport"
        assert exc.retryable

    def test_retry_policy_delays_deterministic(self):
        policy = transport.RetryPolicy(attempts=5, base_delay_seconds=1.0, max_delay_seconds=8.0)
        assert [policy.delay_seconds(a) for a in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 8.0]


class TestExecuteWithRetry:
    def test_transient_then_success_recovers_with_bounded_retries(self):
        calls = []
        sleeps = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(503, "no server available")
            return "ok"

        result = transport.execute_with_retry(flaky, what="probe", sleeper=sleeps.append)
        assert result == "ok"
        assert len(calls) == 3
        assert sleeps == [1.0, 2.0]

    def test_repeated_transient_exhausts_bounded_and_raises_typed_unavailable(self):
        calls = []
        sleeps = []

        def always_broken():
            calls.append(1)
            raise _http_error(503, "no server available")

        with pytest.raises(transport.GitHubUnavailable) as raised:
            transport.execute_with_retry(always_broken, what="probe", sleeper=sleeps.append)
        assert raised.value.attempts == 3
        assert len(calls) == 3
        assert sleeps == [1.0, 2.0]
        assert "probe" in str(raised.value)

    def test_retry_after_honored_and_capped_at_policy_max(self):
        calls = []
        sleeps = []

        def slow_recovery():
            calls.append(1)
            raise _http_error(503, "busy", {"Retry-After": "999"})

        with pytest.raises(transport.GitHubUnavailable):
            transport.execute_with_retry(
                slow_recovery, what="probe", sleeper=sleeps.append, policy=transport.RetryPolicy(attempts=2)
            )
        assert sleeps == [8.0]

    def test_retry_after_seconds_used_when_within_bound(self):
        sleeps = []

        def slow_recovery():
            raise _http_error(503, "busy", {"Retry-After": "3"})

        with pytest.raises(transport.GitHubUnavailable):
            transport.execute_with_retry(
                slow_recovery, what="probe", sleeper=sleeps.append, policy=transport.RetryPolicy(attempts=2)
            )
        assert sleeps == [3.0]

    def test_node_resolution_404_recovers_then_succeeds(self):
        calls = []
        sleeps = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(404, "Could not resolve to a node with the global id of 'PR_x'")
            return "data"

        assert transport.execute_with_retry(flaky, what="reviews", sleeper=sleeps.append) == "data"
        assert sleeps == [1.0]

    def test_node_resolution_404_exhaustion_is_typed_unavailable(self):
        def always_broken():
            raise _http_error(404, "Could not resolve to a node with the global id of 'PR_x'")

        with pytest.raises(transport.GitHubUnavailable) as raised:
            transport.execute_with_retry(always_broken, what="reviews", sleeper=lambda _: None)
        assert raised.value.attempts == 3
        assert "node-resolution" in str(raised.value.last.category)

    def test_permanent_404_never_retried(self):
        sleeps = []

        def not_found():
            raise _http_error(404, "Not Found")

        with pytest.raises(transport.GitHubRequestError) as raised:
            transport.execute_with_retry(not_found, what="probe", sleeper=sleeps.append)
        assert raised.value.category == "permanent"
        assert sleeps == []

    def test_urlerror_transient_then_exhausts(self):
        def broken():
            raise urllib.error.URLError("connection reset")

        with pytest.raises(transport.GitHubUnavailable) as raised:
            transport.execute_with_retry(broken, what="probe", sleeper=lambda _: None)
        assert raised.value.attempts == 3

    def test_malformed_json_retried_then_typed(self):
        def broken():
            raise json.JSONDecodeError("boom", "doc", 0)

        with pytest.raises(transport.GitHubUnavailable):
            transport.execute_with_retry(broken, what="probe", sleeper=lambda _: None)

    def test_typed_retryable_error_retried(self):
        calls = []
        sleeps = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise transport.GitHubRequestError("transient", category="transient", status_code=500)
            return "ok"

        assert transport.execute_with_retry(flaky, what="probe", sleeper=sleeps.append) == "ok"
        assert sleeps == [1.0]

    def test_typed_permanent_error_not_retried(self):
        sleeps = []

        def broken():
            raise transport.GitHubRequestError("permanent", category="permanent", status_code=400)

        with pytest.raises(transport.GitHubRequestError):
            transport.execute_with_retry(broken, what="probe", sleeper=sleeps.append)
        assert sleeps == []

    def test_single_attempt_policy_never_sleeps(self):
        sleeps = []

        def broken():
            raise _http_error(503, "busy")

        with pytest.raises(transport.GitHubUnavailable) as raised:
            transport.execute_with_retry(
                broken, what="probe", sleeper=sleeps.append, policy=transport.RetryPolicy(attempts=1)
            )
        assert raised.value.attempts == 1
        assert sleeps == []
