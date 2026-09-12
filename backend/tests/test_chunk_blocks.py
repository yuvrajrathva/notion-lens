from chunking import chunk_blocks


def _block(block_type, text="", **overrides):
    base = {
        "id": overrides.pop("id", f"blk-{block_type}"),
        "type": block_type,
        "depth": 0,
        "heading_level": None,
        "text": text,
        "language": None,
        "checked": None,
        "list_index": None,
        "has_children": False,
        "cells": None,
        "has_column_header": None,
    }
    base.update(overrides)
    return base


def _heading(level, text, block_id=None):
    return _block(f"heading_{level}", text, heading_level=level, id=block_id or f"h{level}-{text}")


def _para(text, block_id=None):
    return _block("paragraph", text, id=block_id or f"p-{text[:10]}")


def test_empty_blocks_list_returns_no_chunks():
    assert chunk_blocks([], "Some Page") == []


def test_splits_at_heading_boundaries():
    blocks = [
        _heading(1, "Alpha"),
        _para("alpha body"),
        _heading(1, "Beta"),
        _para("beta body"),
    ]
    chunks = chunk_blocks(blocks, "My Page")
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["heading_path"] == ["Alpha"]
    assert "Section: Alpha" in chunks[0]["content"]
    assert "alpha body" in chunks[0]["content"]
    assert chunks[1]["metadata"]["heading_path"] == ["Beta"]
    assert "beta body" in chunks[1]["content"]
    assert "alpha body" not in chunks[1]["content"]


def test_content_before_first_heading_is_not_dropped():
    blocks = [
        _para("leading content"),
        _heading(1, "First Heading"),
        _para("under heading"),
    ]
    chunks = chunk_blocks(blocks, "My Page")
    assert chunks[0]["metadata"]["heading_path"] == []
    assert chunks[0]["content"] == "Page: My Page\n\nleading content"
    assert chunks[1]["metadata"]["heading_path"] == ["First Heading"]


def test_oversized_section_subsplits_with_overlap_and_stays_within_section():
    long_body = "word " * 400  # well over default chunk_size of 1000 chars
    blocks = [
        _heading(1, "Big Section"),
        _para(long_body),
        _heading(1, "Other Section"),
        _para("UNIQUE_MARKER_TEXT"),
    ]
    chunks = chunk_blocks(blocks, "My Page")
    big_section_chunks = [c for c in chunks if c["metadata"]["heading_path"] == ["Big Section"]]
    assert len(big_section_chunks) > 1

    # overlap: tail of chunk i reappears at the head of chunk i+1's body (after the breadcrumb)
    body_0 = big_section_chunks[0]["content"].split("\n\n", 1)[1]
    body_1 = big_section_chunks[1]["content"].split("\n\n", 1)[1]
    assert body_0[-50:] in body_1

    for c in big_section_chunks:
        assert "UNIQUE_MARKER_TEXT" not in c["content"]


def test_code_block_renders_with_language_fence():
    blocks = [_block("code", "print(1)", language="python")]
    chunks = chunk_blocks(blocks, "My Page")
    assert "```python\nprint(1)\n```" in chunks[0]["content"]


def test_table_renders_as_markdown_table_with_header_separator():
    blocks = [
        _block("table", has_column_header=True),
        _block("table_row", cells=["Name", "Age"], id="row-1"),
        _block("table_row", cells=["Alice", "30"], id="row-2"),
    ]
    chunks = chunk_blocks(blocks, "My Page")
    content = chunks[0]["content"]
    assert "| Name | Age |" in content
    assert "| --- | --- |" in content
    assert "| Alice | 30 |" in content
    header_idx = content.index("| Name | Age |")
    sep_idx = content.index("| --- | --- |")
    row_idx = content.index("| Alice | 30 |")
    assert header_idx < sep_idx < row_idx


def test_numbered_list_item_keeps_real_numbers():
    blocks = [
        _block("numbered_list_item", "first", id="n1"),
        _block("numbered_list_item", "second", id="n2"),
        _block("numbered_list_item", "third", id="n3"),
    ]
    # simulate the caller-assigned list_index as notion_client._blocks_to_records would
    for i, b in enumerate(blocks, start=1):
        b["list_index"] = i
    content = chunk_blocks(blocks, "My Page")[0]["content"]
    assert "1. first" in content
    assert "2. second" in content
    assert "3. third" in content


def test_numbered_list_restarts_after_interruption():
    blocks = [
        _block("numbered_list_item", "first", id="n1", list_index=1),
        _block("numbered_list_item", "second", id="n2", list_index=2),
        _para("interruption"),
        _block("numbered_list_item", "third", id="n3", list_index=1),
    ]
    content = chunk_blocks(blocks, "My Page")[0]["content"]
    assert "1. first" in content
    assert "2. second" in content
    assert "1. third" in content


def test_to_do_reflects_checked_state():
    blocks = [
        _block("to_do", "done thing", checked=True, id="t1"),
        _block("to_do", "not done thing", checked=False, id="t2"),
    ]
    content = chunk_blocks(blocks, "My Page")[0]["content"]
    assert "- [x] done thing" in content
    assert "- [ ] not done thing" in content


def test_metadata_contains_breadcrumb_heading_path_block_types_and_ids():
    blocks = [
        _heading(1, "Section A", block_id="head-a"),
        _para("body text", block_id="body-a"),
    ]
    chunk = chunk_blocks(blocks, "My Page")[0]
    metadata = chunk["metadata"]
    assert metadata["breadcrumb"] == "Page: My Page > Section: Section A"
    assert metadata["heading_path"] == ["Section A"]
    assert metadata["block_types"] == ["heading_1", "paragraph"]
    assert metadata["block_ids"] == ["head-a", "body-a"]
