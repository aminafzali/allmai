"""Excel structured intake: parse (openpyxl) -> DuckDB (store) -> query.

- `parse`: workbook bytes -> sheets metadata + RAG summary text.
- `store`: DuckDB build (ingest, worker) + validated read-only queries
  (agent tool). The .duckdb file lives in StorageProvider next to the
  source; only the sheet SUMMARY is embedded for RAG (never raw rows).
"""
