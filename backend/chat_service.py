import uuid

import httpx

import embeddings
import generation
import repositories
from config import settings
from db import SessionLocal
from embeddings import EmbeddingError
from generation import GenerationError

# Prior turns are resent by the frontend each request (no server-side chat
# persistence); cap what we forward to the LLM so a long session can't blow up
# the prompt.
MAX_HISTORY_MESSAGES = 6

SYSTEM_PROMPT = (
    "detailed thinking off\n"
    "You are Notion Lens, an assistant that answers questions using ONLY the "
    "numbered sources given in the user's message. Cite the sources you used "
    "inline with their bracketed numbers, e.g. [1] or [2][3]. If the sources do "
    "not contain the answer, say plainly that your Notion workspace doesn't have "
    "that information — never invent or assume facts that aren't in the sources. "
    "Answer directly, in plain text, with no reasoning steps or preamble."
)

NO_CONTEXT_ANSWER = (
    "I couldn't find anything about that in your Notion workspace. "
    "Try syncing again if you've added or updated relevant pages recently."
)


class ChatError(Exception):
    pass


class NotConnectedError(ChatError):
    pass


# Add citation URL to each chunk
def build_context_block(chunks: list[dict]) -> str:
    sources = []
    for i, chunk in enumerate(chunks, start=1):
        title = chunk.get("title") or "Untitled"
        url = (chunk.get("metadata") or {}).get("url")
        header = f"Source [{i}]: {title}" + (f" ({url})" if url else "")
        sources.append(f"{header}\n{chunk['content']}")
    return "\n\n".join(sources)


def build_messages(question: str, history: list[dict], context_block: str) -> list[dict]:
    trimmed_history = [
        {"role": turn["role"], "content": turn["content"]}
        for turn in history[-MAX_HISTORY_MESSAGES:]
        if turn.get("role") in ("user", "assistant") and turn.get("content")
    ]
    final_user_message = f"Sources:\n{context_block}\n\nQuestion: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *trimmed_history,
        {"role": "user", "content": final_user_message},
    ]


def _build_citations(chunks: list[dict]) -> list[dict]:
    citations = []
    for i, chunk in enumerate(chunks, start=1):
        citations.append(
            {
                "index": i,
                "title": chunk.get("title") or "Untitled",
                "url": (chunk.get("metadata") or {}).get("url"),
                "notion_page_id": chunk.get("notion_page_id"),
            }
        )
    return citations


async def answer_question(app_user_id: uuid.UUID, question: str, history: list[dict]) -> dict:
    """Embeds `question`, retrieves the user's own top-K chunks, and generates a
    grounded answer with citations. Raises NotConnectedError if the user has no
    Notion connection."""
    session = SessionLocal()
    try:
        connection = repositories.get_connection_for_user(session, app_user_id)
        if not connection:
            raise NotConnectedError("No Notion connection found for this user.")

        async with httpx.AsyncClient() as client:
            query_vector = (
                await embeddings.embed_texts(client, [question], input_type="query")
            )[0]
            chunks = repositories.search_similar_chunks(
                session, app_user_id, query_vector, limit=settings.TOP_K_CHUNKS
            )

            if not chunks:
                return {"answer": NO_CONTEXT_ANSWER, "citations": []}

            context_block = build_context_block(chunks)
            messages = build_messages(question, history, context_block)
            answer = await generation.generate_answer(client, messages)

        return {"answer": answer, "citations": _build_citations(chunks)}
    except (EmbeddingError, GenerationError) as exc:
        raise ChatError(str(exc)) from exc
    finally:
        session.close()
