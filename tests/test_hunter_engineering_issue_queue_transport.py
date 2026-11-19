"""Regression: no GitHub endpoint can become a CLI argument (Sonar S8705)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hunter.automation.engineering_issue_queue import QueueAuthorityError


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts/hunter_engineering_issue_queue.py"
    spec = importlib.util.spec_from_file_location("queue_transport", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, body: object, link: str = "") -> None:
        self.data = io.BytesIO(json.dumps(body).encode())
        self.headers = {"Link": link}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.data.close()

    def read(self, size=-1):
        return self.data.read(size)


def test_endpoint_is_never_forwarded_to_cli():
    module = _load()
    with (
        patch.object(module.subprocess, "run") as run,
        patch.object(module.urllib.request, "build_opener") as build_opener,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "secret\n"
        build_opener.return_value.open.return_value = FakeResponse({"ok": True})
        assert module._api("repos/owner/repo") == {"ok": True}
        assert run.call_args.args[0] == ["gh", "auth", "token"]
        assert build_opener.return_value.open.call_args.args[0].full_url == "https://api.github.com/repos/owner/repo"


def test_cross_origin_pagination_fails_closed():
    module = _load()
    with (
        patch.object(module.subprocess, "run") as run,
        patch.object(module.urllib.request, "build_opener") as build_opener,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "secret\n"
        build_opener.return_value.open.return_value = FakeResponse([], '<https://evil.example/steal>; rel="next"')
        with pytest.raises(QueueAuthorityError, match="untrusted GitHub pagination"):
            module._api("repos/owner/repo/issues?per_page=100", paginated=True)
        assert build_opener.return_value.open.call_count == 1


def test_invalid_path_refused():
    module = _load()
    with patch.object(module.subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "secret\n"
        with pytest.raises(QueueAuthorityError, match="invalid GitHub REST path"):
            module._api("--hostname=evil.example")
