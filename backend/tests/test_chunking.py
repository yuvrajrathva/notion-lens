import pytest

from chunking import chunk_text


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_text_shorter_than_chunk_size_is_one_chunk():
    assert chunk_text("hello world", chunk_size=100, overlap=10) == ["hello world"]


def test_fixed_size_windows_with_overlap():
    text = "a" * 25
    chunks = chunk_text(text, chunk_size=10, overlap=2)
    # stride = 8: [0:10], [8:18], [16:25]
    assert chunks == ["a" * 10, "a" * 10, "a" * 9]


def test_chunks_cover_entire_text():
    text = "".join(f"{i:04d}" for i in range(500))  # deterministic long text
    chunks = chunk_text(text, chunk_size=50, overlap=5)
    # every character should appear in at least one chunk
    covered = set()
    pos = 0
    for chunk in chunks:
        idx = text.find(chunk, max(0, pos - 50))
        covered.update(range(idx, idx + len(chunk)))
    assert covered == set(range(len(text)))


def test_chunk_size_must_exceed_overlap():
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=10, overlap=10)
