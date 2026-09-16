from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_requires_ollama_model_and_dedicated_runner_label():
    text = (ROOT / "scripts/bootstrap_hunter_review_runner.sh").read_text()
    assert "qwen2.5-coder:7b" in text
    assert "hunter-reviewer" in text
    assert "svc.sh install" in text or "LaunchAgent" in text


def test_bootstrap_never_echoes_registration_token():
    text = (ROOT / "scripts/bootstrap_hunter_review_runner.sh").read_text()
    assert "set -x" not in text
    assert "echo $TOKEN" not in text
    assert "printf $TOKEN" not in text
