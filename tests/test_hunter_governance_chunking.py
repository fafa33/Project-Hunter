"""Unit tests for deterministic, lossless diff chunking."""

from __future__ import annotations

import pytest
from hunter_governance_review.chunking import covered_filenames, split_diff_into_chunks

SAMPLE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "index 2222222..3333333 100644\n"
    "--- a/src/b.py\n"
    "+++ b/src/b.py\n"
    "@@ -1 +1 @@\n"
    "-old2\n"
    "+new2\n"
)


def test_empty_diff_produces_zero_chunks() -> None:
    assert split_diff_into_chunks("", 1000) == []
    assert split_diff_into_chunks("   \n", 1000) == []


def test_small_diff_produces_one_chunk_covering_all_files() -> None:
    chunks = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=10_000)
    assert len(chunks) == 1
    assert chunks[0].index == 1
    assert chunks[0].total == 1
    assert set(chunks[0].files) == {"src/a.py", "src/b.py"}
    assert chunks[0].text == SAMPLE_DIFF


def test_chunking_is_byte_exact_reconstruction() -> None:
    chunks = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=30)
    reconstructed = "".join(chunk.text for chunk in chunks)
    assert reconstructed == SAMPLE_DIFF


def test_chunking_never_exceeds_max_chunk_chars() -> None:
    chunks = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=40)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 40


def test_chunking_is_deterministic() -> None:
    first = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=40)
    second = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=40)
    assert first == second


def test_oversized_single_file_is_split_not_truncated() -> None:
    huge_file_diff = "diff --git a/big.py b/big.py\n" + ("+line of content\n" * 5_000)
    chunks = split_diff_into_chunks(huge_file_diff, max_chunk_chars=500)
    assert len(chunks) > 1
    reconstructed = "".join(chunk.text for chunk in chunks)
    assert reconstructed == huge_file_diff
    for chunk in chunks:
        assert chunk.files == ("big.py",)


def test_single_line_longer_than_budget_is_hard_split_not_lost() -> None:
    huge_line_diff = "diff --git a/x.py b/x.py\n+" + ("a" * 1000) + "\n"
    chunks = split_diff_into_chunks(huge_line_diff, max_chunk_chars=100)
    reconstructed = "".join(chunk.text for chunk in chunks)
    assert reconstructed == huge_line_diff


def test_covered_filenames_spans_all_chunks() -> None:
    chunks = split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=40)
    assert covered_filenames(chunks) == {"src/a.py", "src/b.py"}


def test_max_chunk_chars_must_be_positive() -> None:
    with pytest.raises(ValueError):
        split_diff_into_chunks(SAMPLE_DIFF, max_chunk_chars=0)


def test_unparsed_diff_without_file_headers_is_still_covered() -> None:
    weird = "not a real unified diff, just some text"
    chunks = split_diff_into_chunks(weird, max_chunk_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].text == weird
