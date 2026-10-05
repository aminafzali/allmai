"""Unit tests for the deterministic benchmark math (no DB, no network)."""

from eval.retrieval_bench import score_row, score_unanswerable, summarize


def test_score_row_recall_mrr_coverage():
    returned = [
        {"source": "other.pdf", "text": "هوای امروز آفتابی است"},
        {"source": "acids.pdf", "text": "اسیدها پروتون می‌دهند"},
    ]
    out = score_row(returned, ["acids.pdf", "missing.pdf"], ["اسیدها", "پروتون", "nope"])
    assert out["recall_at_k"] == 0.5
    assert out["mrr"] == 0.5
    assert out["keyword_coverage"] == round(2 / 3, 4)
    assert out["hits"] == 2


def test_score_row_empty_expectations_is_vacuous():
    out = score_row([{"source": "a", "text": "t"}], [], [])
    # empty relevance set: recall vacuous, precision 0 (nothing is relevant)
    assert out["recall_at_k"] == 1.0 and out["precision_at_k"] == 0.0
    assert out["ndcg_at_k"] == 1.0 and out["mrr"] == 0.0


def test_score_row_precision_and_ndcg():
    returned = [
        {"source": "other.pdf", "text": "هوای امروز آفتابی است"},
        {"source": "acids.pdf", "text": "اسیدها پروتون می‌دهند"},
        {"source": "acids.pdf", "text": "بازها هیدروکسید دارند"},
    ]
    out = score_row(returned, ["acids.pdf"], ["اسیدها"])
    assert out["recall_at_k"] == 1.0  # two chunks, one distinct file
    assert out["precision_at_k"] == round(2 / 3, 4)
    # relevance [0,1,1]: DCG = 0 + 1/log2(3) + 1/2 ; IDCG = 1 + 1/log2(3)
    import math

    ideal = 1.0 + 1.0 / math.log2(3)
    got = (1.0 / math.log2(3) + 0.5) / ideal
    assert out["ndcg_at_k"] == round(got, 4)
    assert out["mrr"] == 0.5
    # perfect ranking scores 1.0
    perfect = score_row(list(reversed(returned)), ["acids.pdf"], ["اسیدها"])
    assert perfect["ndcg_at_k"] == 1.0 and perfect["mrr"] == 1.0


def test_score_unanswerable_track():
    out = score_unanswerable([{"source": "a", "text": "t"}], 0.042)
    assert out == {"empty": False, "top_score": 0.042, "hits": 1}
    assert score_unanswerable([], 0.0)["empty"] is True


def test_summarize_groups_by_type():
    rows = [
        {"recall_at_k": 1.0, "mrr": 1.0, "keyword_coverage": 1.0, "type": "factual"},
        {"recall_at_k": 0.0, "mrr": 0.0, "keyword_coverage": 0.5, "type": "factual"},
        {"recall_at_k": 0.5, "mrr": 0.5, "keyword_coverage": 0.5, "type": "adversarial"},
    ]
    s = summarize(rows)
    assert s["n"] == 3 and s["avg_recall_at_k"] == 0.5
    assert s["recall_by_type"] == {"factual": 0.5, "adversarial": 0.5}
