from datetime import timezone

from notion_client import extract_title, parse_timestamp


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
