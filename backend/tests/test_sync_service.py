from datetime import datetime, timedelta, timezone

import pytest

import embeddings
import notion_client
import repositories
import sync_service
from db import SessionLocal
from models import NotionConnection


def _para_blocks(text: str) -> list[dict]:
    if not text:
        return []
    return [
        {
            "id": "blk-0",
            "type": "paragraph",
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
    ]


def _make_page(page_id: str, title: str, last_edited: datetime) -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "archived": False,
        "parent": {"type": "workspace", "workspace": True},
        "last_edited_time": last_edited.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
    }


class FakeNotion:
    """In-memory stand-in for the Notion API surface sync_service depends on."""

    def __init__(self, pages: list[dict], page_text: dict[str, str]):
        self.pages = pages  # must already be sorted last_edited_time descending
        self.page_text = page_text
        self.search_calls = 0
        self.content_fetch_calls: list[str] = []

    async def search_pages(self, client, access_token):
        self.search_calls += 1
        for page in self.pages:
            yield page

    async def get_page_blocks(self, client, access_token, page_id):
        self.content_fetch_calls.append(page_id)
        return _para_blocks(self.page_text.get(page_id, ""))


class FakeEmbedder:
    def __init__(self):
        self.embed_calls = 0
        self.texts_embedded: list[str] = []

    async def embed_texts(self, client, texts, input_type="passage"):
        self.embed_calls += 1
        self.texts_embedded.extend(texts)
        return [[0.0] * 2048 for _ in texts]


@pytest.fixture
def wired_connection(db_session, make_app_user):
    user_id = make_app_user()
    connection = repositories.upsert_connection(
        db_session, user_id, "ws-sync-test", "Test Workspace", None, "fake-token"
    )
    db_session.commit()
    return user_id, connection.id


def _get_connection(connection_id):
    session = SessionLocal()
    try:
        return session.get(NotionConnection, connection_id)
    finally:
        session.close()


async def test_full_sync_indexes_all_pages_and_sets_checkpoint(monkeypatch, wired_connection):
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)
    pages = [
        _make_page("page-b", "Newer Page", now),
        _make_page("page-a", "Older Page", now - timedelta(hours=1)),
    ]
    fake_notion = FakeNotion(pages, {"page-b": "b " * 300, "page-a": "a " * 300})
    fake_embedder = FakeEmbedder()

    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder.embed_texts)

    await sync_service.run_sync(user_id)

    connection = _get_connection(connection_id)
    assert connection.last_synced_at is not None
    assert abs((connection.last_synced_at - now).total_seconds()) < 1
    assert fake_embedder.embed_calls == 2  # one page has content -> chunks; both pages have content here
    assert set(fake_notion.content_fetch_calls) == {"page-a", "page-b"}


async def test_incremental_sync_skips_unchanged_pages(monkeypatch, wired_connection):
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)

    # First sync: one page.
    pages = [_make_page("page-a", "Original", now - timedelta(hours=2))]
    fake_notion = FakeNotion(pages, {"page-a": "content " * 200})
    fake_embedder = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder.embed_texts)

    await sync_service.run_sync(user_id)
    assert fake_embedder.embed_calls == 1

    # Second sync: Notion has nothing newer than the checkpoint -> no re-embedding.
    fake_embedder_2 = FakeEmbedder()
    fake_notion_2 = FakeNotion(pages, {"page-a": "content " * 200})
    monkeypatch.setattr(notion_client, "search_pages", fake_notion_2.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion_2.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder_2.embed_texts)

    await sync_service.run_sync(user_id)

    assert fake_embedder_2.embed_calls == 0
    assert fake_notion_2.content_fetch_calls == []


async def test_incremental_sync_reembeds_only_changed_pages(monkeypatch, wired_connection):
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)

    pages_v1 = [
        _make_page("page-a", "A", now - timedelta(hours=2)),
        _make_page("page-b", "B", now - timedelta(hours=3)),
    ]
    fake_notion = FakeNotion(pages_v1, {"page-a": "aaa " * 200, "page-b": "bbb " * 200})
    fake_embedder = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder.embed_texts)
    await sync_service.run_sync(user_id)
    assert fake_embedder.embed_calls == 2

    # page-a is edited again (newer than checkpoint); page-b is untouched.
    pages_v2 = [
        _make_page("page-a", "A updated", now + timedelta(minutes=5)),
        _make_page("page-b", "B", now - timedelta(hours=3)),
    ]
    fake_notion_2 = FakeNotion(pages_v2, {"page-a": "aaa updated " * 200, "page-b": "bbb " * 200})
    fake_embedder_2 = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion_2.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion_2.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder_2.embed_texts)

    await sync_service.run_sync(user_id)

    assert fake_notion_2.content_fetch_calls == ["page-a"]
    assert fake_embedder_2.embed_calls == 1

    connection = _get_connection(connection_id)
    assert abs((connection.last_synced_at - (now + timedelta(minutes=5))).total_seconds()) < 1


async def test_noop_sync_still_advances_checkpoint(monkeypatch, wired_connection):
    """A successful sync that finds nothing new must still bump last_synced_at —
    otherwise auto-sync's "at most once per day" throttle would treat a quiet
    connection as perpetually overdue and re-trigger on every check."""
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)
    pages = [_make_page("page-a", "A", now - timedelta(hours=2))]
    fake_notion = FakeNotion(pages, {"page-a": "content " * 200})
    fake_embedder = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder.embed_texts)

    await sync_service.run_sync(user_id)  # first sync: indexes page-a

    before_second_sync = datetime.now(timezone.utc)
    fake_notion_2 = FakeNotion(pages, {"page-a": "content " * 200})
    fake_embedder_2 = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion_2.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion_2.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder_2.embed_texts)

    await sync_service.run_sync(user_id)  # second sync: nothing new

    connection = _get_connection(connection_id)
    assert connection.last_synced_at >= before_second_sync


async def test_sync_picks_up_newly_shared_page_older_than_checkpoint(monkeypatch, wired_connection):
    """A page the user just shared with the integration might not have been
    edited recently — its last_edited_time can sort anywhere in Notion's
    descending page list regardless of when access was granted. Sync must
    still find it (by scanning the full list every run), while skipping the
    already-known, unchanged page without re-fetching or re-embedding it."""
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)
    pages = [_make_page("page-a", "A", now - timedelta(hours=2))]
    fake_notion = FakeNotion(pages, {"page-a": "content " * 200})
    fake_embedder = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder.embed_texts)

    await sync_service.run_sync(user_id)  # first sync: indexes page-a, sets checkpoint

    # Simulate the user granting access to an old page ("page-old") that predates
    # the checkpoint, alongside the already-known, unchanged page-a.
    old_page = _make_page("page-old", "Old Page", now - timedelta(days=30))
    fake_notion_2 = FakeNotion(
        [*pages, old_page], {"page-a": "content " * 200, "page-old": "old content " * 200}
    )
    fake_embedder_2 = FakeEmbedder()
    monkeypatch.setattr(notion_client, "search_pages", fake_notion_2.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion_2.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embedder_2.embed_texts)

    await sync_service.run_sync(user_id)

    assert fake_notion_2.content_fetch_calls == ["page-old"]  # page-a skipped, unchanged
    assert fake_embedder_2.embed_calls == 1


async def test_sync_failure_does_not_advance_checkpoint(monkeypatch, wired_connection):
    user_id, connection_id = wired_connection
    now = datetime.now(timezone.utc)
    pages = [_make_page("page-a", "A", now)]
    fake_notion = FakeNotion(pages, {"page-a": "aaa " * 200})

    async def failing_embed(client, texts, input_type="passage"):
        raise embeddings.EmbeddingError("simulated failure")

    monkeypatch.setattr(notion_client, "search_pages", fake_notion.search_pages)
    monkeypatch.setattr(notion_client, "get_page_blocks", fake_notion.get_page_blocks)
    monkeypatch.setattr(embeddings, "embed_texts", failing_embed)

    await sync_service.run_sync(user_id)

    connection = _get_connection(connection_id)
    assert connection.last_synced_at is None

    from sync_status import get_status

    assert get_status(str(user_id))["state"] == "error"
