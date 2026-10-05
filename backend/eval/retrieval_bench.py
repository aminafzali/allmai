"""Deterministic retrieval benchmark (P2, no LLM).

Golden row (JSONL, one object per line):
  {"question": "...", "kb_id": "<uuid>", "expected_sources": ["a.pdf"],
   "expected_keywords": ["اسید", "pH"], "type": "factual|multi|caption|adversarial"}
  Unanswerable rows: "type": "unanswerable" (expected_* empty) — the right
  answer does not exist in the KB. Scored separately: we record whether
  retrieval stayed empty and its top score (feeds future min_score tuning).

Metrics per answerable row (pure, unit-tested):
- recall@k, precision@k, nDCG@k (binary relevance on expected_sources),
  keyword_coverage, top_score.
Latency per row: retrieval_ms (fusion, no rerank), rerank_ms (per arm),
total_ms.

Arms: --methods none,api runs every row under both rerank arms on the
same candidate set and reports deltas. The api arm makes real AvalAI
calls (key from server settings); fail-open keeps it safe.

Run from backend/: .venv/Scripts/python -m eval.retrieval_bench golden.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time


def _is_rel(name: str, expected: list[str]) -> bool:
    return any(s == name or (s and s in name) for s in expected)


def _dcg(relevances: list[int]) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))


def score_row(returned: list[dict], expected_sources: list[str],
              expected_keywords: list[str]) -> dict:
    """Pure metric math. returned: [{source: filename, text: content}]."""
    names = [str(r.get("source") or "") for r in returned]
    exp = [str(s or "") for s in (expected_sources or [])]
    rel = [1 if _is_rel(n, exp) else 0 for n in names]
    # recall counts DISTINCT expected sources (ten chunks from the same
    # file are one hit, not ten) — precision/nDCG keep per-chunk credit.
    found = len({s for s in exp if any(s == n or (s and s in n) for n in names)})
    recall = (found / len(exp)) if exp else 1.0
    precision = (sum(rel) / len(names)) if names else (1.0 if not exp else 0.0)
    ideal = sorted(rel, reverse=True)
    idcg = _dcg(ideal)
    ndcg = (_dcg(rel) / idcg) if idcg else (1.0 if not exp else 0.0)
    rank = next((i for i, n in enumerate(names, 1) if _is_rel(n, exp)), 0)
    mrr = (1.0 / rank) if rank else 0.0
    blob = "\n".join(str(r.get("text") or "") for r in returned)
    kws = [str(k or "") for k in (expected_keywords or [])]
    cov = (sum(1 for k in kws if k and k in blob) / len(kws)) if kws else 1.0
    return {"recall_at_k": round(recall, 4), "precision_at_k": round(precision, 4),
            "ndcg_at_k": round(ndcg, 4), "mrr": round(mrr, 4),
            "keyword_coverage": round(cov, 4), "hits": len(names)}


def score_unanswerable(returned: list[dict], top_score: float) -> dict:
    """Separate track: did retrieval stay empty, and how hot was its best
    candidate? High top_scores here argue for a future min_score."""
    return {"empty": not returned, "top_score": round(top_score or 0.0, 4),
            "hits": len(returned)}


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if not r.get("unanswerable")]
    out: dict = {"n": len(rows), "n_answerable": len(ans),
                 "n_unanswerable": len(rows) - len(ans)}
    for key in ("recall_at_k", "precision_at_k", "ndcg_at_k", "mrr",
                "keyword_coverage", "retrieval_ms", "rerank_ms", "total_ms"):
        vals = [r[key] for r in ans if isinstance(r.get(key), (int, float))]
        if vals:
            out[f"avg_{key}"] = round(sum(vals) / len(vals), 2)
    if ans:
        by_type: dict[str, list[float]] = {}
        for r in ans:
            by_type.setdefault(str(r.get("type") or "?"), []).append(r["recall_at_k"])
        out["recall_by_type"] = {t: round(sum(v) / len(v), 4) for t, v in by_type.items()}
    un = [r for r in rows if r.get("unanswerable")]
    if un:
        out["unanswerable_empty_rate"] = round(
            sum(1 for r in un if r.get("empty")) / len(un), 4)
        scores = [r.get("top_score", 0.0) for r in un]
        out["unanswerable_avg_top_score"] = round(sum(scores) / len(scores), 4)
    return out


def _answerable_row(returned, row, lat: dict) -> dict:
    scored = score_row(returned, row.get("expected_sources") or [],
                       row.get("expected_keywords") or [])
    scored.update(type=row.get("type", "?"), question=row["question"][:80],
                  unanswerable=False, **lat)
    return scored


def run(golden_path: str, methods=("none", "api"), top_k: int = 8,
        fake_vectors: bool = False) -> dict:
    from app.common.base import coerce_uuid
    from app.core.database import SessionLocal
    from app.knowledge.models import KnowledgeBase

    rows = []
    with open(golden_path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    raise SystemExit(f"{golden_path}:{ln}: bad JSON: {exc}")
    db = SessionLocal()
    try:
        # RLS is FORCED on kb tables: the benchmark reads cross-workspace
        # golden rows, so it runs with the admin GUC (read-only workload).
        from sqlalchemy import text as _text

        db.execute(_text("SET app.is_admin = 'true'"))
        from app.knowledge.retrieval.hybrid import hybrid_search
        from app.knowledge.retrieval.rerank import AvalAIReranker

        arms: dict[str, object] = {"none": None, "api": AvalAIReranker()}
        for m in methods:
            if m not in arms:
                raise SystemExit(f"unknown method arm: {m} (use none,api)")
        out_rows = []
        for row in rows:
            kb = db.query(KnowledgeBase).filter(
                KnowledgeBase.id == coerce_uuid(row["kb_id"])).one()
            kw: dict = {"top_k": top_k, "rerank_fn": None}
            if fake_vectors:
                dim = 1536
                kw["query_vector"] = [0.0] * dim
                kw["embed_fn"] = lambda texts, _d=dim: [[0.0] * _d for _ in texts]
            t0 = time.perf_counter()
            hits = hybrid_search(db, kb.workspace_id, row["question"],
                                 kb_id=row["kb_id"], **kw)
            t1 = time.perf_counter()
            base = [{"cid": str(h.chunk.id),
                     "source": (h.source.filename if h.source else ""),
                     "text": h.chunk.content or "",
                     "score": h.score or 0.0} for h in hits]
            top_score = max([b["score"] for b in base] or [0.0])
            unans = (row.get("type") == "unanswerable"
                     or bool(row.get("expect_empty")))
            for m in methods:
                lat = {"retrieval_ms": round((t1 - t0) * 1000, 1)}
                if unans:
                    scored = score_unanswerable(base, top_score)
                    scored.update(type=row.get("type", "unanswerable"),
                                  question=row["question"][:80],
                                  unanswerable=True, arm=m,
                                  rerank_ms=0.0, total_ms=lat["retrieval_ms"])
                    out_rows.append(scored)
                    continue
                fn = arms[m]
                r0 = time.perf_counter()
                _rerr = ""
                try:
                    final = list(fn(row["question"], list(hits), top_k)[:top_k]) if fn else list(hits)
                except Exception as exc:
                    _rerr = f"{type(exc).__name__}: {exc}"[:300]
                    raise
                finally:
                    r1 = time.perf_counter()
                    if fn is not None:
                        # P3 ledger: the measured rerank arm bills here,
                        # attributed to the row's workspace (P4 evidence).
                        from app.usage import recorder as _urec

                        _urec.record_event(
                            "rerank",
                            provider=getattr(fn, "name", "api") or "api",
                            model=getattr(fn, "model", None),
                            workspace_id=kb.workspace_id,
                            input_chars=sum(len(h.chunk.content or "")
                                            for h in hits),
                            latency_ms=round((r1 - r0) * 1000, 1),
                            ok=not _rerr, error=_rerr,
                            meta={"candidates": len(hits),
                                  "final_k": top_k, "bench": True})
                # keep reranked order, not fused order (by chunk identity)
                by_cid = {b["cid"]: b for b in base}
                ret = [{"source": by_cid[str(h.chunk.id)]["source"],
                        "text": by_cid[str(h.chunk.id)]["text"]}
                       for h in final if str(h.chunk.id) in by_cid]
                lat.update(rerank_ms=round((r1 - r0) * 1000, 1))
                lat["total_ms"] = round(lat["retrieval_ms"] + lat["rerank_ms"], 1)
                scored = _answerable_row(ret, row, lat)
                scored["arm"] = m
                out_rows.append(scored)
    finally:
        db.close()
    return {"rows": out_rows, "summary": _summarize_arms(out_rows)}


def _summarize_arms(rows: list[dict]) -> dict:
    arms: dict[str, list[dict]] = {}
    for r in rows:
        arms.setdefault(str(r.get("arm", "?")), []).append(r)
    out = {arm: summarize(rs) for arm, rs in arms.items()}
    if "none" in out and "api" in out:
        delta = {}
        for k, v in out["api"].items():
            if k.startswith("avg_") and isinstance(v, (int, float)) \
                    and isinstance(out["none"].get(k), (int, float)):
                delta[k] = round(v - out["none"][k], 4)
        out["delta_api_minus_none"] = delta
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="deterministic retrieval benchmark")
    ap.add_argument("golden", help="golden JSONL path")
    ap.add_argument("--methods", default="none,api",
                    help="comma subset of none,api (default: both arms)")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--fake-vectors", action="store_true",
                    help="offline smoke only (zero vectors): scores are meaningless")
    ap.add_argument("--out", default="", help="write Markdown summary here")
    ap.add_argument("--json-out", default="",
                    help="write full JSON report here (stdout stays ASCII-safe)")
    args = ap.parse_args(argv)
    methods = tuple(m.strip() for m in args.methods.split(",") if m.strip())
    report = run(args.golden, methods=methods, top_k=args.top_k,
                 fake_vectors=args.fake_vectors)
    json_path = args.json_out or (args.golden + ".report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("report rows=%d json=%s" % (len(report["rows"]), json_path))
    print(json.dumps(report["summary"], ensure_ascii=True, indent=2))
    if args.out:
        lines = ["# Retrieval baseline", ""]
        for arm, summ in report["summary"].items():
            lines.append(f"## {arm}")
            if isinstance(summ, dict):
                for k, v in summ.items():
                    lines.append(f"- {k}: {v}")
            else:
                lines.append(f"- {summ}")
            lines.append("")
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
