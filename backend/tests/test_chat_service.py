import uuid

import pytest

import chat_service
import embeddings
import generation
import notion_client
import repositories


@pytest.fixture
def wired_page(db_session, make_app_user):
    user_id = make_app_user()
    connection = repositories.upsert_connection(
        db_session, user_id, "ws-chat-test", "Test Workspace", None, "fake-token"
    )
    db_session.commit()
    page = repositories.upsert_page(
        db_session,
        connection.id,
        "page-paris",
        "France",
        notion_client.utcnow(),
        {"url": "https://notion.so/page-paris"},
    )
    db_session.commit()
    repositories.replace_chunks(
        db_session,
        page.id,
        [{"chunk_index": 0, "content": "Paris is the capital of France.", "embedding": [0.1] * 2048}],
    )
    db_session.commit()
    return user_id


def test_build_messages_trims_history_and_includes_context():
    long_history = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    messages = chat_service.build_messages("What is X?", long_history, "Source [1]: foo\nbar")

    assert messages[0]["role"] == "system"
    assert len(messages) == 1 + chat_service.MAX_HISTORY_MESSAGES + 1
    assert messages[-1]["role"] == "user"
    assert "Source [1]: foo" in messages[-1]["content"]
    assert "What is X?" in messages[-1]["content"]


async def test_answer_question_returns_grounded_answer_with_citations(monkeypatch, wired_page):
    user_id = wired_page

    async def fake_embed_texts(client, texts, input_type="passage"):
        assert input_type == "query"
        return [[0.1] * 2048]

    captured_messages = {}

    async def fake_generate_answer(client, messages, temperature=0.2, max_tokens=1024):
        captured_messages["messages"] = messages
        return "The capital of France is Paris. [1]"

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(generation, "generate_answer", fake_generate_answer)

    result = await chat_service.answer_question(user_id, "What is the capital of France?", [])

    assert result["answer"] == "The capital of France is Paris. [1]"
    assert len(result["citations"]) == 1
    assert result["citations"][0]["title"] == "France"
    assert result["citations"][0]["url"] == "https://notion.so/page-paris"
    assert result["citations"][0]["notion_page_id"] == "page-paris"
    assert "Paris is the capital of France." in captured_messages["messages"][-1]["content"]


async def test_answer_question_short_circuits_when_nothing_indexed(monkeypatch, db_session, make_app_user):
    user_id = make_app_user()
    repositories.upsert_connection(db_session, user_id, "ws-empty", "Empty Workspace", None, "fake-token")
    db_session.commit()

    async def fake_embed_texts(client, texts, input_type="passage"):
        return [[0.1] * 2048]

    generate_calls = []

    async def fake_generate_answer(client, messages, temperature=0.2, max_tokens=1024):
        generate_calls.append(messages)
        return "should not be called"

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(generation, "generate_answer", fake_generate_answer)

    result = await chat_service.answer_question(user_id, "Anything?", [])

    assert result["answer"] == chat_service.NO_CONTEXT_ANSWER
    assert result["citations"] == []
    assert generate_calls == []


async def test_answer_question_raises_when_not_connected():
    with pytest.raises(chat_service.NotConnectedError):
        await chat_service.answer_question(uuid.uuid4(), "Anything?", [])
