import uuid

import httpx

import chunking
import embeddings
import notion_client
import repositories
import sync_status
from db import SessionLocal
from embeddings import EmbeddingError
from notion_client import NotionAPIError


class SyncError(Exception):
    pass


async def run_sync(app_user_id: uuid.UUID) -> None:
    """Runs a full (first-ever) or incremental sync for the given app user's Notion
    connection. Meant to run as a background task — progress and the final result are
    reported through `sync_status`, not a return value."""
    key = str(app_user_id)
    sync_status.set_status(
        key,
        state="running",
        pages_processed=0,
        chunks_embedded=0,
        message=None,
        started_at=notion_client.utcnow().isoformat(),
        finished_at=None,
    )

    session = SessionLocal()
    try:
        connection = repositories.get_connection_for_user(session, app_user_id)
        if not connection:
            raise SyncError("No Notion connection found for this user.")

        connection_id = connection.id
        access_token = connection.access_token
        since = connection.last_synced_at

        pages_processed = 0
        chunks_embedded = 0
        max_last_edited = None

        async with httpx.AsyncClient() as client:
            async for page in notion_client.search_pages(client, access_token, since=since):
                notion_page_id = page["id"]
                last_edited_time = notion_client.parse_timestamp(page["last_edited_time"])
                title = notion_client.extract_title(page)
                metadata = {
                    "url": page.get("url"),
                    "archived": page.get("archived", False),
                    "parent": page.get("parent"),
                }

                text = await notion_client.get_page_plain_text(client, access_token, notion_page_id)
                chunks = chunking.chunk_text(text)
                vectors = await embeddings.embed_texts(client, chunks, input_type="passage") if chunks else []

                page_row = repositories.upsert_page(
                    session,
                    connection_id=connection_id,
                    notion_page_id=notion_page_id,
                    title=title,
                    last_edited_time=last_edited_time,
                    metadata=metadata,
                )
                repositories.replace_chunks(
                    session,
                    page_id=page_row.id,
                    chunks=[
                        {"chunk_index": i, "content": content, "embedding": vector}
                        for i, (content, vector) in enumerate(zip(chunks, vectors))
                    ],
                )
                session.commit()

                pages_processed += 1
                chunks_embedded += len(chunks)
                if max_last_edited is None or last_edited_time > max_last_edited:
                    max_last_edited = last_edited_time

                sync_status.set_status(key, pages_processed=pages_processed, chunks_embedded=chunks_embedded)

        if max_last_edited is not None:
            repositories.mark_synced(session, connection_id, max_last_edited)
            session.commit()

        sync_status.set_status(
            key,
            state="success",
            pages_processed=pages_processed,
            chunks_embedded=chunks_embedded,
            finished_at=notion_client.utcnow().isoformat(),
            message=f"Synced {pages_processed} page(s), {chunks_embedded} chunk(s).",
        )
    except (SyncError, NotionAPIError, EmbeddingError) as exc:
        session.rollback()
        sync_status.set_status(
            key, state="error", message=str(exc), finished_at=notion_client.utcnow().isoformat()
        )
    except Exception as exc:  # noqa: BLE001 - surface unexpected errors to the frontend instead of hanging
        session.rollback()
        sync_status.set_status(
            key,
            state="error",
            message=f"Unexpected error: {exc}",
            finished_at=notion_client.utcnow().isoformat(),
        )
    finally:
        session.close()
