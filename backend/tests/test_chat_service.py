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


@pytest.fixture
def wired_page_multi_chunk(db_session, make_app_user):
    user_id = make_app_user()
    connection = repositories.upsert_connection(
        db_session, user_id, "ws-chat-multi-test", "Test Workspace", None, "fake-token"
    )
    db_session.commit()
    page = repositories.upsert_page(
        db_session,
        connection.id,
        "page-france",
        "France",
        notion_client.utcnow(),
        {"url": "https://notion.so/page-france"},
    )
    db_session.commit()
    repositories.replace_chunks(
        db_session,
        page.id,
        [
            {"chunk_index": 0, "content": "Paris is the capital of France.", "embedding": [0.1] * 2048},
            {"chunk_index": 1, "content": "France is located in Western Europe.", "embedding": [0.1] * 2048},
        ],
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


def test_group_chunks_by_page_groups_same_page_preserving_order():
    a1 = {"notion_page_id": "A", "content": "a1"}
    b1 = {"notion_page_id": "B", "content": "b1"}
    a2 = {"notion_page_id": "A", "content": "a2"}

    groups = chat_service._group_chunks_by_page([a1, b1, a2])

    assert groups == [[a1, a2], [b1]]


def test_group_chunks_by_page_single_chunk_per_page_is_pass_through():
    chunks = [{"notion_page_id": str(i), "content": str(i)} for i in range(3)]

    groups = chat_service._group_chunks_by_page(chunks)

    assert groups == [[c] for c in chunks]


def test_group_chunks_by_page_group_count_equals_unique_page_count():
    chunks = [
        {"notion_page_id": "A", "content": "a1"},
        {"notion_page_id": "A", "content": "a2"},
        {"notion_page_id": "B", "content": "b1"},
        {"notion_page_id": "C", "content": "c1"},
        {"notion_page_id": "A", "content": "a3"},
    ]

    groups = chat_service._group_chunks_by_page(chunks)

    assert len(groups) == len({c["notion_page_id"] for c in chunks})


def test_build_context_block_merges_multi_chunk_page_into_one_source():
    page_groups = [
        [
            {"notion_page_id": "A", "title": "Page A", "metadata": {"url": "https://x/a"}, "content": "first chunk"},
            {"notion_page_id": "A", "title": "Page A", "metadata": {"url": "https://x/a"}, "content": "second chunk"},
        ]
    ]

    block = chat_service.build_context_block(page_groups)

    assert block.count("Source [1]:") == 1
    assert "first chunk" in block
    assert "second chunk" in block


def test_build_citations_one_entry_per_page_group():
    page_groups = [
        [{"notion_page_id": "A", "title": "Page A", "metadata": {"url": "https://x/a"}}],
        [{"notion_page_id": "B", "title": "Page B", "metadata": {"url": "https://x/b"}}],
    ]

    citations = chat_service._build_citations(page_groups)

    assert len(citations) == 2
    assert citations[0]["index"] == 1
    assert citations[0]["notion_page_id"] == "A"
    assert citations[1]["index"] == 2
    assert citations[1]["notion_page_id"] == "B"


async def test_answer_question_collapses_same_page_chunks_into_one_citation(
    monkeypatch, wired_page_multi_chunk
):
    user_id = wired_page_multi_chunk

    async def fake_embed_texts(client, texts, input_type="passage"):
        return [[0.1] * 2048]

    captured_messages = {}

    async def fake_generate_answer(client, messages, temperature=0.2, max_tokens=1024):
        captured_messages["messages"] = messages
        return "France is in Western Europe and its capital is Paris. [1]"

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(generation, "generate_answer", fake_generate_answer)

    result = await chat_service.answer_question(user_id, "Tell me about France.", [])

    assert len(result["citations"]) == 1
    assert result["citations"][0]["notion_page_id"] == "page-france"
    prompt_content = captured_messages["messages"][-1]["content"]
    assert "Paris is the capital of France." in prompt_content
    assert "France is located in Western Europe." in prompt_content


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
