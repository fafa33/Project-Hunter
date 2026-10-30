import json
from types import SimpleNamespace

import pytest
from hunter_issue_evidence_writer import EvidenceWriteError, publish


def test_create_then_read_after_write_and_repeat_without_second_post():
    comments = []
    posts = []

    def run(argv, **kwargs):
        if "POST" in argv:
            posts.append(argv)
            comments.append(
                {"body": argv[-1][5:], "html_url": "https://github.com/example/repo/issues/3#issuecomment-4"}
            )
            data = comments[-1]
        else:
            data = comments
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))

    for _ in range(2):
        assert publish("example/repo", 3, "proof-12345", "42 passed", run=run).endswith("issuecomment-4")
    assert len(posts) == 1


def test_failed_write_does_not_retry_or_change_channel():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(EvidenceWriteError):
        publish("example/repo", 3, "proof-12345", "evidence", run=run)
    assert len(calls) == 1


def test_invalid_scope_fails_before_io():
    with pytest.raises(ValueError):
        publish("bad;repo", 3, "proof-12345", "evidence")


def test_evidence_file_rejects_traversal_and_absolute_paths(tmp_path):
    from hunter_issue_evidence_writer import read_evidence_file

    (tmp_path / "evidence.txt").write_text("verified")
    assert read_evidence_file("evidence.txt", root=tmp_path) == "verified"
    for path in ("../secret", str(tmp_path / "evidence.txt")):
        with pytest.raises(ValueError):
            read_evidence_file(path, root=tmp_path)


def test_evidence_file_rejects_symlink_and_directory(tmp_path):
    from hunter_issue_evidence_writer import read_evidence_file

    (tmp_path / "real.txt").write_text("verified")
    (tmp_path / "link.txt").symlink_to("real.txt")
    (tmp_path / "folder").mkdir()
    for path in ("link.txt", "folder"):
        with pytest.raises((OSError, ValueError)):
            read_evidence_file(path, root=tmp_path)
