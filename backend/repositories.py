import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
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
