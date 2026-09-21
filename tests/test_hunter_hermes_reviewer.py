import json
import subprocess

import hunter_hermes_reviewer as hermes


def test_json_parser_accepts_pure_or_last_line_json():
    payload = {"verdict": "clear", "summary": "No blocking defects", "findings": []}
    assert hermes._json_from_stdout(json.dumps(payload)) == payload
    assert hermes._json_from_stdout("log line\n" + json.dumps(payload)) == payload


def test_hermes_review_runs_safe_cli_and_validates_contract(monkeypatch):
    monkeypatch.setattr(hermes, "hermes_binary", lambda: "/tmp/hermes")
    seen = {}
    def run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"verdict":"clear","summary":"No blocking defects","findings":[]}), stderr="")
    monkeypatch.setattr(hermes.subprocess, "run", run)
    result = hermes.hermes_review("diff --git a/a b/a")
    assert result["verdict"] == "clear"
    assert "--safe-mode" in seen["command"] and "--cli" in seen["command"] and "-z" in seen["command"]
