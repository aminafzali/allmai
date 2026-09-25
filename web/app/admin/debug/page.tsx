"use client";

import { useState } from "react";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

export default function DebugPage() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      setResult(
        await api(`/workspaces/${ws}/knowledge/debug`, {
          method: "POST",
          json: { query, kb_id: kb || null, top_k: 8 },
        })
      );
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">دیباگر retrieval</h1>
      <form onSubmit={run} className="mt-4 flex flex-col gap-2">
        <div className="flex gap-2">
          <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
          <KBSelect wsId={ws} value={kb} onChange={setKb} />
        </div>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border px-3 py-1"
            placeholder="سؤال تست…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit" disabled={busy || !ws}>
            {busy ? "…" : "اجرا"}
          </button>
        </div>
      </form>
      <FormError e={error} />
      {result && (
        <div className="mt-4 space-y-4 text-sm">
          <p>مدل: <code dir="ltr">{result.model}</code></p>
          <section>
            <h2 className="font-bold">چانک‌های بازیابی‌شده ({result.retrieved.length})</h2>
            <ul className="mt-2 space-y-2">
              {result.retrieved.map((c: any) => (
                <li key={c.chunk_id} className="rounded border bg-white p-3">
                  <div className="text-xs text-slate-500" dir="ltr">
                    score {c.score} · vec #{c.vector_rank ?? "—"} · fts #{c.fts_rank ?? "—"} · {c.filename}
                    {c.page_no ? ` · p.${c.page_no}` : ""}
                    {c.start_ms != null ? ` · ${c.start_ms}-${c.end_ms}ms` : ""}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap">{c.content.slice(0, 800)}</p>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2 className="font-bold">کانتکست ارسالی به مدل</h2>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-100 p-3 text-xs">
              {result.context}
            </pre>
          </section>
          <section>
            <h2 className="font-bold">پاسخ مدل</h2>
            <p className="mt-2 whitespace-pre-wrap rounded border bg-white p-3">{result.answer}</p>
          </section>
        </div>
      )}
    </main>
  );
}
