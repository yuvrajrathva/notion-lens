from typing import Any, Hashable


def reciprocal_rank_fusion(
    vector_results: list[dict],
    keyword_results: list[dict],
    k: int = 60,
    limit: int = 5,
) -> list[dict]:
    """Merges two ranked result lists (each row a dict with an "id" key) via
    Reciprocal Rank Fusion: score(doc) = sum, over each list the doc appears
    in, of 1 / (k + rank), where rank is the doc's 1-indexed position in that
    list (0 contribution from a list it's absent from). k=60 is the standard
    RRF constant from the fusion literature. Pure function - no DB/HTTP
    dependency."""
    scores: dict[Hashable, float] = {}
    rows: dict[Hashable, dict[str, Any]] = {}

    for result_list in (vector_results, keyword_results):
        for rank, row in enumerate(result_list, start=1):
            doc_id = row["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            rows.setdefault(doc_id, row)

    fused = sorted(rows.values(), key=lambda row: scores[row["id"]], reverse=True)
    return [{**row, "rrf_score": scores[row["id"]]} for row in fused[:limit]]
