from ranking import reciprocal_rank_fusion


def _row(doc_id, **extra):
    return {"id": doc_id, **extra}


def test_doc_in_both_lists_outranks_doc_in_only_one_list():
    vector_results = [_row("a"), _row("b"), _row("c")]
    keyword_results = [_row("b"), _row("x"), _row("y")]

    fused = reciprocal_rank_fusion(vector_results, keyword_results, limit=10)

    ids = [row["id"] for row in fused]
    assert ids[0] == "b"  # present in both lists -> highest combined score


def test_degrades_to_vector_order_when_keyword_list_empty():
    vector_results = [_row("a"), _row("b"), _row("c")]

    fused = reciprocal_rank_fusion(vector_results, [], limit=10)

    assert [row["id"] for row in fused] == ["a", "b", "c"]


def test_respects_limit():
    vector_results = [_row(str(i)) for i in range(10)]
    keyword_results = [_row(str(i)) for i in range(10, 20)]

    fused = reciprocal_rank_fusion(vector_results, keyword_results, limit=5)

    assert len(fused) == 5


def test_dedupes_docs_present_in_both_lists():
    vector_results = [_row("a", content="from vector")]
    keyword_results = [_row("a", content="from vector"), _row("b", content="keyword only")]

    fused = reciprocal_rank_fusion(vector_results, keyword_results, limit=10)

    ids = [row["id"] for row in fused]
    assert ids.count("a") == 1
    assert set(ids) == {"a", "b"}


def test_output_rows_carry_original_columns_plus_rrf_score():
    vector_results = [_row("a", content="hello", title="Page A")]

    fused = reciprocal_rank_fusion(vector_results, [], limit=10)

    assert fused[0]["content"] == "hello"
    assert fused[0]["title"] == "Page A"
    assert "rrf_score" in fused[0]


def test_k_changes_score_magnitude_not_relative_order():
    vector_results = [_row("a"), _row("b")]
    keyword_results = [_row("b"), _row("a")]

    fused_k1 = reciprocal_rank_fusion(vector_results, keyword_results, k=1, limit=10)
    fused_k60 = reciprocal_rank_fusion(vector_results, keyword_results, k=60, limit=10)

    assert [row["id"] for row in fused_k1] == [row["id"] for row in fused_k60]
    assert fused_k1[0]["rrf_score"] != fused_k60[0]["rrf_score"]
