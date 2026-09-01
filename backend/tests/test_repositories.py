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
