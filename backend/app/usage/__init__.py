"""Internal tracing + usage ledger (P3, no Langfuse).

- context: trace/workspace/user propagation (contextvars) + per-request
  trace middleware.
- recorder: single fail-safe entry point; every span (agent turn,
  retrieval, rerank, model call, extraction) lands here with token
  counts so future subscription billing can aggregate per user or
  per workspace. Billing itself is NOT implemented.
- pricing: static per-model USD estimates (admin-adjustable later).
- service/router: minimal read surface (admin + workspace summaries).
"""
