from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_title(page: dict) -> Optional[str]:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            rich_text = prop.get("title", [])
            text = "".join(t.get("plain_text", "") for t in rich_text)
            return text or None
    return None


async def search_pages(client: httpx.AsyncClient, access_token: str) -> AsyncIterator[dict]:
    """Yields every Notion page object the integration can currently see,
    sorted by last_edited_time descending.

    Always walks the full result set rather than stopping at the first
    already-known page: last_edited_time can't distinguish "unchanged since
    last sync" from "just shared with the integration but not recently
    edited," so a newly-granted old page can sort anywhere in this list. The
    caller (sync_service) decides which pages are worth re-fetching based on
    what it already has stored — this call is cheap (metadata only, no block
    or embedding fetches), so scanning it in full every run is fine.
    """
    cursor = None
    while True:
        body = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor

        resp = await client.post(
            f"{NOTION_API_BASE}/search", json=body, headers=_headers(access_token), timeout=30
        )
        if resp.status_code != 200:
            raise NotionAPIError(resp.status_code, f"Notion search failed: {resp.text}")

        data = resp.json()
        for page in data.get("results", []):
            yield page

        if not data.get("has_more"):
            return
        cursor = data.get("next_cursor")


async def _fetch_block_children(client: httpx.AsyncClient, access_token: str, block_id: str) -> list[dict]:
    blocks = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        resp = await client.get(
            f"{NOTION_API_BASE}/blocks/{block_id}/children",
            headers=_headers(access_token),
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            raise NotionAPIError(resp.status_code, f"Notion block children fetch failed: {resp.text}")

        data = resp.json()
        blocks.extend(data.get("results", []))

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return blocks


def _block_to_record(block: dict, depth: int) -> dict:
    """Pure extraction: one raw Notion block dict -> one structured record.
    No markdown rendering and no sibling/ordering knowledge here (list_index
    is filled in by the caller, which owns sequencing across siblings)."""
    block_type = block.get("type")
    payload = block.get(block_type, {})

    heading_level = None
    if block_type in ("heading_1", "heading_2", "heading_3"):
        heading_level = int(block_type[-1])

    if block_type == "table_row":
        cells = ["".join(t.get("plain_text", "") for t in cell) for cell in payload.get("cells", [])]
        text = ""
    else:
        cells = None
        rich_text = payload.get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rich_text)

    return {
        "id": block.get("id"),
        "type": block_type,
        "depth": depth,
        "heading_level": heading_level,
        "text": text,
        "language": payload.get("language") if block_type == "code" else None,
        "checked": payload.get("checked") if block_type == "to_do" else None,
        "list_index": None,
        "has_children": bool(block.get("has_children")),
        "cells": cells,
        "has_column_header": payload.get("has_column_header") if block_type == "table" else None,
    }


async def _blocks_to_records(client: httpx.AsyncClient, access_token: str, block_id: str, depth: int = 0) -> list[dict]:
    if depth > 10:
        return []

    records = []
    numbered_run = 0
    for block in await _fetch_block_children(client, access_token, block_id):
        record = _block_to_record(block, depth)

        if record["type"] == "numbered_list_item":
            numbered_run += 1
            record["list_index"] = numbered_run
        else:
            numbered_run = 0

        records.append(record)

        if block.get("has_children"):
            records.extend(await _blocks_to_records(client, access_token, block["id"], depth + 1))

    return records


async def get_page_blocks(client: httpx.AsyncClient, access_token: str, page_id: str) -> list[dict]:
    return await _blocks_to_records(client, access_token, page_id)


async def get_owner_email(client: httpx.AsyncClient, access_token: str) -> Optional[str]:
    resp = await client.get(f"{NOTION_API_BASE}/users/me", headers=_headers(access_token), timeout=15)
    if resp.status_code != 200:
        raise NotionAPIError(resp.status_code, f"Notion users/me failed: {resp.text}")

    data = resp.json()
    owner = data.get("bot", {}).get("owner", {})
    if owner.get("type") != "user":
        return None
    return owner.get("user", {}).get("person", {}).get("email")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
