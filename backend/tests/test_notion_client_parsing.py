from datetime import timezone

from notion_client import _block_to_record, extract_title, parse_timestamp


def test_parse_timestamp_handles_z_suffix():
    dt = parse_timestamp("2024-05-01T12:30:00.000Z")
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 12


def test_extract_title_finds_title_property_regardless_of_name():
    page = {
        "properties": {
            "Tags": {"type": "multi_select", "multi_select": []},
            "Name": {
                "type": "title",
                "title": [{"plain_text": "Hello "}, {"plain_text": "World"}],
            },
        }
    }
    assert extract_title(page) == "Hello World"


def test_extract_title_returns_none_when_missing():
    page = {"properties": {"Tags": {"type": "multi_select", "multi_select": []}}}
    assert extract_title(page) is None


def test_extract_title_returns_none_for_empty_title():
    page = {"properties": {"title": {"type": "title", "title": []}}}
    assert extract_title(page) is None


def test_block_to_record_extracts_heading_level():
    block = {
        "id": "blk-1",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"plain_text": "Intro"}]},
    }
    record = _block_to_record(block, depth=0)
    assert record["heading_level"] == 2
    assert record["text"] == "Intro"


def test_block_to_record_non_heading_has_no_heading_level():
    block = {"id": "blk-1", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "hi"}]}}
    record = _block_to_record(block, depth=0)
    assert record["heading_level"] is None


def test_block_to_record_extracts_code_language():
    block = {
        "id": "blk-1",
        "type": "code",
        "code": {"rich_text": [{"plain_text": "print(1)"}], "language": "python"},
    }
    record = _block_to_record(block, depth=0)
    assert record["language"] == "python"
    assert record["text"] == "print(1)"


def test_block_to_record_extracts_todo_checked_state():
    block = {
        "id": "blk-1",
        "type": "to_do",
        "to_do": {"rich_text": [{"plain_text": "Buy milk"}], "checked": True},
    }
    record = _block_to_record(block, depth=0)
    assert record["checked"] is True


def test_block_to_record_extracts_table_row_cells():
    block = {
        "id": "blk-1",
        "type": "table_row",
        "table_row": {
            "cells": [
                [{"plain_text": "Name"}],
                [{"plain_text": "Age"}],
            ]
        },
    }
    record = _block_to_record(block, depth=0)
    assert record["cells"] == ["Name", "Age"]
    assert record["text"] == ""


def test_block_to_record_extracts_table_has_column_header():
    block = {
        "id": "blk-1",
        "type": "table",
        "table": {"has_column_header": True, "table_width": 2},
    }
    record = _block_to_record(block, depth=0)
    assert record["has_column_header"] is True
