from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Block types whose rich_text we render with a simple prefix, for slightly more
# legible chunks. Anything not listed here still gets its rich_text extracted plainly.
_BLOCK_PREFIXES = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "- ",
    "to_do": "- ",
    "quote": "> ",
}


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


async def search_pages(
    client: httpx.AsyncClient, access_token: str, since: Optional[datetime] = None
) -> AsyncIterator[dict]:
    """Yields Notion page objects sorted by last_edited_time descending.

    If `since` is given, stops as soon as a page at or older than `since` is seen
    (safe because results are sorted descending), so only changed pages are yielded.
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
            last_edited = parse_timestamp(page["last_edited_time"])
            if since is not None and last_edited <= since:
                return
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


async def _blocks_to_text(client: httpx.AsyncClient, access_token: str, block_id: str, depth: int = 0) -> str:
    if depth > 10:
        return ""

    lines = []
    for block in await _fetch_block_children(client, access_token, block_id):
        block_type = block.get("type")
        payload = block.get(block_type, {})
        rich_text = payload.get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rich_text)

        if text:
            prefix = _BLOCK_PREFIXES.get(block_type, "")
            lines.append(f"{prefix}{text}")
        elif block_type == "code" and payload.get("rich_text"):
            lines.append(text)

        if block.get("has_children"):
            child_text = await _blocks_to_text(client, access_token, block["id"], depth + 1)
            if child_text:
                lines.append(child_text)

    return "\n".join(lines)


async def get_page_plain_text(client: httpx.AsyncClient, access_token: str, page_id: str) -> str:
    return await _blocks_to_text(client, access_token, page_id)


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
