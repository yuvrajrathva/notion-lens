import uuid

import pytest

import notion_client
import repositories
from models import AppUser


def test_upsert_app_user_creates_then_updates_same_id(db_session, make_app_user):
    user_id = uuid.uuid4()
    repositories.upsert_app_user(db_session, user_id, "a@example.com")
    db_session.commit()

    repositories.upsert_app_user(db_session, user_id, "a-updated@example.com")
    db_session.commit()

    user = db_session.get(AppUser, user_id)
    assert user.email == "a-updated@example.com"

    db_session.delete(user)
    db_session.commit()


def test_upsert_app_user_rejects_duplicate_email_under_different_id(db_session, make_app_user):
    existing_id = make_app_user(email="shared@example.com")

    with pytest.raises(repositories.EmailAlreadyLinkedError):
        repositories.upsert_app_user(db_session, uuid.uuid4(), "shared@example.com")


def test_upsert_connection_is_idempotent_per_workspace(db_session, make_app_user):
    user_id = make_app_user()

    conn1 = repositories.upsert_connection(
        db_session, user_id, "ws-1", "My Workspace", None, "token-a"
    )
    db_session.commit()

    conn2 = repositories.upsert_connection(
        db_session, user_id, "ws-1", "My Workspace Renamed", None, "token-b"
    )
    db_session.commit()

    assert conn1.id == conn2.id
    assert conn2.workspace_name == "My Workspace Renamed"
    assert conn2.access_token == "token-b"


def test_replace_chunks_removes_stale_chunks(db_session, make_app_user):
    user_id = make_app_user()
    connection = repositories.upsert_connection(db_session, user_id, "ws-2", "WS", None, "token")
    db_session.commit()

    page = repositories.upsert_page(
        db_session,
        connection.id,
        "page-1",
        "Title",
        notion_client.utcnow(),
        {},
    )
    db_session.commit()

    dim = 2048
    repositories.replace_chunks(
        db_session,
        page.id,
        [
            {"chunk_index": 0, "content": "first", "embedding": [0.0] * dim},
            {"chunk_index": 1, "content": "second", "embedding": [0.0] * dim},
        ],
    )
    db_session.commit()
    assert len(page.chunks) == 2

    # A re-chunk into a single, smaller chunk should leave exactly one row behind.
    repositories.replace_chunks(
        db_session,
        page.id,
        [{"chunk_index": 0, "content": "only one now", "embedding": [0.0] * dim}],
    )
    db_session.commit()
    db_session.refresh(page)
    assert [c.content for c in page.chunks] == ["only one now"]


def _unit_vector(dim: int, *hot_indices: int) -> list[float]:
    """A vector with 1.0 at each given index (normalized), 0.0 elsewhere."""
    vec = [0.0] * dim
    for i in hot_indices:
        vec[i] = 1.0
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def _make_chunk_page(db_session, user_id, workspace_id, content, embedding, dim=2048):
    connection = repositories.upsert_connection(db_session, user_id, workspace_id, "WS", None, "token")
    db_session.commit()
    page = repositories.upsert_page(
        db_session, connection.id, f"page-{workspace_id}", "Title", notion_client.utcnow(), {"url": f"https://notion.so/{workspace_id}"}
    )
    db_session.commit()
    repositories.replace_chunks(
        db_session, page.id, [{"chunk_index": 0, "content": content, "embedding": embedding}]
    )
    db_session.commit()
    return connection, page


def test_search_similar_chunks_orders_by_cosine_similarity(db_session, make_app_user):
    dim = 2048
    user_id = make_app_user()

    query = _unit_vector(dim, 0)
    close = _unit_vector(dim, 0)
    mid = _unit_vector(dim, 0, 1)
    far = _unit_vector(dim, 1)

    _make_chunk_page(db_session, user_id, "ws-close", "close chunk", close, dim)
    _make_chunk_page(db_session, user_id, "ws-mid", "mid chunk", mid, dim)
    _make_chunk_page(db_session, user_id, "ws-far", "far chunk", far, dim)

    results = repositories.search_similar_chunks(db_session, user_id, query, limit=5)

    assert [r["content"] for r in results] == ["close chunk", "mid chunk", "far chunk"]
    assert results[0]["similarity"] > results[1]["similarity"] > results[2]["similarity"]


def test_search_similar_chunks_is_scoped_to_the_calling_user(db_session, make_app_user):
    dim = 2048
    user_a = make_app_user()
    user_b = make_app_user()

    query = _unit_vector(dim, 0)
    # user_b's chunk is a perfect match for the query, but must never appear
    # in user_a's results.
    _make_chunk_page(db_session, user_a, "ws-a", "user a chunk", _unit_vector(dim, 1), dim)
    _make_chunk_page(db_session, user_b, "ws-b", "user b chunk", _unit_vector(dim, 0), dim)

    results = repositories.search_similar_chunks(db_session, user_a, query, limit=5)

    assert len(results) == 1
    assert results[0]["content"] == "user a chunk"
