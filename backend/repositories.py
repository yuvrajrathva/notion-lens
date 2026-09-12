import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AppUser, NotionConnection, NotionChunk, NotionPage


class EmailAlreadyLinkedError(Exception):
    """Raised when the Notion account's email is already linked to a different app user id."""


def upsert_app_user(session: Session, user_id: uuid.UUID, email: str) -> AppUser:
    user = session.get(AppUser, user_id)
    if user:
        user.email = email
        session.flush()
        return user

    user = AppUser(id=user_id, email=email)
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise EmailAlreadyLinkedError(email) from exc
    return user


def upsert_connection(
    session: Session,
    app_user_id: uuid.UUID,
    workspace_id: str,
    workspace_name: Optional[str],
    workspace_icon: Optional[str],
    access_token: str,
) -> NotionConnection:
    connection = session.execute(
        select(NotionConnection).where(
            NotionConnection.app_user_id == app_user_id,
            NotionConnection.notion_workspace_id == workspace_id,
        )
    ).scalar_one_or_none()

    if connection:
        connection.access_token = access_token
        connection.workspace_name = workspace_name
        connection.workspace_icon = workspace_icon
    else:
        connection = NotionConnection(
            app_user_id=app_user_id,
            notion_workspace_id=workspace_id,
            workspace_name=workspace_name,
            workspace_icon=workspace_icon,
            access_token=access_token,
        )
        session.add(connection)

    session.flush()
    return connection


def get_connection_for_user(session: Session, app_user_id: uuid.UUID) -> Optional[NotionConnection]:
    """Phase 1 assumes a single Notion workspace connection per app user."""
    return session.execute(
        select(NotionConnection)
        .where(NotionConnection.app_user_id == app_user_id)
        .order_by(NotionConnection.created_at.desc())
    ).scalars().first()


def list_all_connections(session: Session) -> list[NotionConnection]:
    return session.execute(select(NotionConnection)).scalars().all()


# Raw SQL (rather than the ORM's `Vector.cosine_distance()`) so the query text
# matches `ix_notion_chunks_embedding_cosine`'s indexed expression exactly
# (`embedding::halfvec(2048)`) — pgvector's native `vector` ANN indexes cap at
# 2000 dims, so that index is built on a halfvec cast, and only a query that
# casts the same way will use it instead of falling back to a seq scan.
_SEARCH_SIMILAR_CHUNKS_SQL = text(
    """
    SELECT c.id, c.content, c.chunk_index, p.id AS page_id, p.title,
           p.notion_page_id, p.metadata,
           1 - (c.embedding::halfvec(2048) <=> CAST(:qvec AS halfvec(2048))) AS similarity
    FROM notion_chunks c
    JOIN notion_pages p ON p.id = c.page_id
    JOIN notion_connections conn ON conn.id = p.connection_id
    WHERE conn.app_user_id = :app_user_id AND c.embedding IS NOT NULL
    ORDER BY c.embedding::halfvec(2048) <=> CAST(:qvec AS halfvec(2048))
    LIMIT :limit
    """
)


def search_similar_chunks(
    session: Session, app_user_id: uuid.UUID, query_vector: list[float], limit: int = 5
) -> list[dict]:
    """Top-`limit` chunks by cosine similarity, scoped to the given user's own
    Notion connection(s) only."""
    qvec = "[" + ",".join(repr(x) for x in query_vector) + "]"
    # Our WHERE clause filters by app_user_id, but an HNSW index has no notion of
    # that filter — by default Postgres traverses the graph for a bounded number
    # of candidates and only *then* applies the WHERE clause, so a user with few
    # chunks relative to the rest of the table can silently get back fewer than
    # `limit` rows (or zero). `strict_order` iterative scan (pgvector >= 0.8) makes
    # it keep traversing until it has `limit` *filtered* matches, in true distance
    # order. SET LOCAL scopes this to the current transaction only.
    session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    rows = session.execute(
        _SEARCH_SIMILAR_CHUNKS_SQL,
        {"qvec": qvec, "app_user_id": app_user_id, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def get_public_status(session: Session, app_user_id: uuid.UUID) -> Optional[dict]:
    connection = get_connection_for_user(session, app_user_id)
    if not connection:
        return None
    return {
        "workspace_name": connection.workspace_name,
        "workspace_icon": connection.workspace_icon,
        "connected_at": connection.created_at.isoformat(),
        "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
    }


def mark_synced(session: Session, connection_id: uuid.UUID, synced_at: datetime) -> None:
    connection = session.get(NotionConnection, connection_id)
    if connection:
        connection.last_synced_at = synced_at
        session.flush()


def get_last_edited_map(session: Session, connection_id: uuid.UUID) -> dict[str, datetime]:
    """notion_page_id -> last_edited_time for every page already indexed under
    this connection. sync_service uses this to decide, for each page Notion's
    full search results return, whether it's already up to date (skip) or new
    / changed (fetch content and re-embed)."""
    rows = session.execute(
        select(NotionPage.notion_page_id, NotionPage.last_edited_time).where(
            NotionPage.connection_id == connection_id
        )
    ).all()
    return {notion_page_id: last_edited_time for notion_page_id, last_edited_time in rows}


def upsert_page(
    session: Session,
    connection_id: uuid.UUID,
    notion_page_id: str,
    title: Optional[str],
    last_edited_time: datetime,
    metadata: dict,
) -> NotionPage:
    page = session.execute(
        select(NotionPage).where(
            NotionPage.connection_id == connection_id,
            NotionPage.notion_page_id == notion_page_id,
        )
    ).scalar_one_or_none()

    if page:
        page.title = title
        page.last_edited_time = last_edited_time
        page.metadata_ = metadata
    else:
        page = NotionPage(
            connection_id=connection_id,
            notion_page_id=notion_page_id,
            title=title,
            last_edited_time=last_edited_time,
            metadata_=metadata,
        )
        session.add(page)

    session.flush()
    return page


def replace_chunks(session: Session, page_id: uuid.UUID, chunks: list[dict]) -> None:
    """Deletes existing chunks for a page and inserts the freshly embedded ones."""
    session.query(NotionChunk).filter(NotionChunk.page_id == page_id).delete()
    for item in chunks:
        session.add(
            NotionChunk(
                page_id=page_id,
                chunk_index=item["chunk_index"],
                content=item["content"],
                embedding=item["embedding"],
                metadata_=item.get("metadata"),
            )
        )
    session.flush()
