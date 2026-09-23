from pathlib import Path

WORKFLOW = Path(".github/workflows/codeql-default-setup-bridge.yml").read_text()


def test_codeql_bridge_retries_transient_api_and_parse_failures():
    assert 'if ! analyses="$(gh api' in WORKFLOW
    assert "CodeQL API unavailable on attempt" in WORKFLOW
    assert 'if ! current="$(jq -c' in WORKFLOW
    assert "CodeQL API returned malformed analysis data on attempt" in WORKFLOW


def test_codeql_bridge_still_fails_on_real_analysis_error():
    assert "CodeQL default-setup analysis failed:" in WORKFLOW
    assert "exit 1" in WORKFLOW
