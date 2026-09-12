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
    reported through `sync_status`, not a return value.

    Scans Notion's full page list every run (cheap: metadata only — see
    notion_client.search_pages) rather than stopping once last_edited_time
    drops below the last checkpoint. That early-stop would miss a page the
    user just shared with the integration but hasn't edited recently: its
    last_edited_time can sort anywhere in the descending list, and Notion's
    API has no separate "granted to integration at" timestamp to filter on.
    Only pages that are new, or whose last_edited_time is newer than what's
    already stored, get fetched and re-embedded — everything else is skipped
    before any expensive Notion/embedding call, so this stays "incremental"
    in cost even though it's no longer incremental in what it scans.
    """
    key = str(app_user_id)
    sync_started_at = notion_client.utcnow()
    sync_status.set_status(
        key,
        state="running",
        pages_processed=0,
        chunks_embedded=0,
        message=None,
        started_at=sync_started_at.isoformat(),
        finished_at=None,
    )

    session = SessionLocal()
    try:
        connection = repositories.get_connection_for_user(session, app_user_id)
        if not connection:
            raise SyncError("No Notion connection found for this user.")

        connection_id = connection.id
        access_token = connection.access_token
        known_pages = repositories.get_last_edited_map(session, connection_id)

        pages_processed = 0
        chunks_embedded = 0
        max_last_edited = None

        async with httpx.AsyncClient() as client:
            async for page in notion_client.search_pages(client, access_token):
                notion_page_id = page["id"]
                last_edited_time = notion_client.parse_timestamp(page["last_edited_time"])

                known_edited = known_pages.get(notion_page_id)
                if known_edited is not None and known_edited >= last_edited_time:
                    continue  # already indexed and unchanged

                title = notion_client.extract_title(page)
                metadata = {
                    "url": page.get("url"),
                    "archived": page.get("archived", False),
                    "parent": page.get("parent"),
                }

                blocks = await notion_client.get_page_blocks(client, access_token, notion_page_id)
                chunk_records = chunking.chunk_blocks(blocks, page_title=title)
                texts = [record["content"] for record in chunk_records]
                vectors = await embeddings.embed_texts(client, texts, input_type="passage") if texts else []

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
                        {
                            "chunk_index": i,
                            "content": record["content"],
                            "embedding": vector,
                            "metadata": record["metadata"],
                        }
                        for i, (record, vector) in enumerate(zip(chunk_records, vectors))
                    ],
                )
                session.commit()

                pages_processed += 1
                chunks_embedded += len(chunk_records)
                if max_last_edited is None or last_edited_time > max_last_edited:
                    max_last_edited = last_edited_time

                sync_status.set_status(key, pages_processed=pages_processed, chunks_embedded=chunks_embedded)

        # Even a no-op run (nothing newer than the checkpoint) is a successful sync
        # attempt and must advance last_synced_at — otherwise a connection with no
        # new content would look perpetually "overdue" and get re-triggered on
        # every app-open / scheduler sweep instead of at most once a day.
        repositories.mark_synced(session, connection_id, max_last_edited or sync_started_at)
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
