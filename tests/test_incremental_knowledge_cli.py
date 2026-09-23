from pathlib import Path

SCRIPT = Path("scripts/hunter_incremental_knowledge.py")


def test_learning_cli_has_stdout_only_no_registry_write_path():
    text = SCRIPT.read_text()
    assert "--output" not in text
    assert ".write_text(" not in text
    assert "print(json.dumps(ledger" in text


def test_learning_cli_has_no_caller_selected_historical_or_registry_paths():
    text = SCRIPT.read_text()
    assert '"--historical"' not in text
    assert '"--registry"' not in text
