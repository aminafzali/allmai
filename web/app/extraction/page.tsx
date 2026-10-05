"use client";

import { useEffect, useState } from "react";
import { API_BASE, api, getToken, type StreamEvent } from "@/lib/admin";
import { streamChatResolvingTools } from "@/lib/clientToolLoop";
import LeadsPanel from "@/components/LeadsPanel";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

const KEY = "data_extraction_assistant";
const CONV_KEY = "allmai_extract_conv";

type Msg = {
  role: string;
  content: string;
  citations?: any[];
  file?: { label: string; convId: string };
};
type Status = "idle" | "sending" | "searching" | "streaming" | "error" | "done";
type Tab = "chat" | "results";

export default function ExtractionApp() {
  const [ws, setWs] = useState("");
  const [tab, setTab] = useState<Tab>("chat");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [creating, setCreating] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Msg[]>([]);
  const [activity, setActivity] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<string | null>(null);

  useEffect(() => {
    if (!ws) {
      setAgents([]);
      setAgentId("");
      return;
    }
    api(`/workspaces/${ws}/agents`)
      .then((list: any[]) => {
        setAgents(list);
        const found = list.find((a) => a.key === KEY);
        setAgentId(found ? found.id : "");
      })
      .catch(() => {});
    setConvId(localStorage.getItem(CONV_KEY));
  }, [ws]);

  async function createAgent() {
    if (!ws || creating) return;
    setCreating(true);
    setError("");
    try {
      const a = await api(`/workspaces/${ws}/agents`, {
        method: "POST",
        json: { key: KEY, type: "agent", name: "دستیار استخراج داده" },
      });
      setAgents((l) => [...l, a]);
      setAgentId(a.id);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setCreating(false);
    }
  }

  async function downloadConversation(conv: string, fmt: "csv" | "xlsx") {
    const r = await fetch(
      `${API_BASE}/workspaces/${ws}/leads/export?format=${fmt}&conversation_id=${conv}`,
      { headers: { Authorization: `Bearer ${getToken() ?? ""}` } }
    );
    if (!r.ok) {
      setError(`download failed: ${r.status}`);
      return;
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fmt === "xlsx" ? `leads-${conv.slice(0, 8)}.xlsx` : `leads-${conv.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function send(text: string) {
    if (!text.trim() || !agentId) return;
    setError("");
    setLastFailed(null);
    setStatus("sending");
    setActivity([]);
    setHistory((h) => [...h, { role: "user", content: text }]);
    setMessage("");
    let acc = "";
    let cits: any[] = [];
    let cid: string | null = convId;
    let saved = 0;
    try {
      await streamChatResolvingTools(
        `/workspaces/${ws}/agents/${agentId}/chat/stream`,
        `/workspaces/${ws}/agents/${agentId}/chat/resume`,
        { message: text, kb_id: null, conversation_id: convId },
        (e: StreamEvent) => {
          if (e.type === "meta") {
            cid = e.conversation_id;
            setConvId(e.conversation_id);
            localStorage.setItem(CONV_KEY, e.conversation_id);
            cits = e.citations ?? [];
          } else if (e.type === "searching") {
            setStatus("searching");
          } else if (e.type === "progress") {
            const line = `[${e.stage}] ${e.detail}`;
            setActivity((a) => [...a, line]);
          } else if (e.type === "token") {
            setStatus("streaming");
            acc += e.text;
            const snapshot = acc;
            setHistory((h) => {
              const last = h[h.length - 1];
              if (last?.role === "assistant-stream") {
                return [...h.slice(0, -1), { role: "assistant-stream", content: snapshot }];
              }
              return [...h, { role: "assistant-stream", content: snapshot }];
            });
          } else if (e.type === "done") {
            cid = e.conversation_id;
            setConvId(e.conversation_id);
            localStorage.setItem(CONV_KEY, e.conversation_id);
            cits = e.citations ?? cits;
            saved = e.leads_saved ?? 0;
            const final = acc;
            const file = saved > 0 && cid
              ? { label: `${saved} لید ذخیره شد`, convId: cid }
              : undefined;
            setHistory((h) => {
              const last = h[h.length - 1];
              const done: Msg = { role: "assistant", content: final, citations: cits, file };
              return last?.role === "assistant-stream" ? [...h.slice(0, -1), done] : [...h, done];
            });
            setStatus("done");
          } else if (e.type === "error") {
            throw new Error(e.message);
          }
        }
      );
    } catch (err: any) {
      setStatus("error");
      setError(err?.name === "AbortError" ? "قطع شد." : String(err?.message ?? err));
      setLastFailed(text);
      setHistory((h) => {
        const last = h[h.length - 1];
        return last?.role === "assistant-stream" ? h.slice(0, -1) : h;
      });
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    void send(message);
  }

  function newChat() {
    setConvId(null);
    setHistory([]);
    setStatus("idle");
    setError("");
    setLastFailed(null);
    localStorage.removeItem(CONV_KEY);
  }

  const busy = status === "sending" || status === "streaming" || status === "searching";

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-xl font-bold">دستیار استخراج داده</h1>
      <p className="mt-1 text-xs text-slate-500">
        جستجوی وب و مکان در مرورگر شما اجرا می‌شود؛ لیدها در دیتابیس ذخیره می‌شوند.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <WorkspaceSelect value={ws} onChange={setWs} />
        {ws && !agentId && (
          <button
            className="rounded bg-violet-700 px-3 py-1 text-sm text-white disabled:opacity-50"
            onClick={createAgent}
            disabled={creating}
          >
            {creating ? "…" : "ساخت دستیار استخراج"}
          </button>
        )}
        {convId && tab === "chat" && (
          <button className="rounded border px-3 py-1 text-sm" onClick={newChat}>
            گفتگوی جدید
          </button>
        )}
      </div>
      <FormError e={error} />

      <div className="mt-4 flex gap-1 border-b text-sm">
        {(["chat", "results"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-t border-x border-t px-4 py-2 ${tab === t ? "border-b-white bg-white font-bold" : "bg-slate-50 text-slate-500"}`}
          >
            {t === "chat" ? "گفتگو" : "نتایج داده"}
          </button>
        ))}
      </div>

      {tab === "results" ? (
        <LeadsPanel wsId={ws} />
      ) : (
        <>
          <div className="mt-4 space-y-3" aria-live="polite">
            {history.length === 0 && (
              <p className="text-center text-sm text-slate-400">
                مثلاً: «رستوران‌های ایتالیایی تهران با تلفن» یا «قیمت امروز دلار»
              </p>
            )}
            {history.map((m, i) => (
              <div
                key={i}
                className={`rounded-2xl p-3 text-sm ${m.role === "user" ? "ml-12 bg-violet-700 text-white" : "mr-12 border bg-white"}`}
              >
                <p className="whitespace-pre-wrap">{m.content}{m.role === "assistant-stream" && <span className="animate-pulse"> ▍</span>}</p>
                {!!m.citations?.length && (
                  <p className="mt-2 text-[11px] opacity-70" dir="ltr">
                    {m.citations.map((c: any) => `[${c.n}] ${c.source ?? ""}`).join(" · ")}
                  </p>
                )}
                {m.file && (
                  <button
                    onClick={() => downloadConversation(m.file!.convId, "csv")}
                    className="mt-2 flex items-center gap-2 rounded-lg border border-green-300 bg-green-50 px-3 py-2 text-xs text-green-900 hover:bg-green-100"
                  >
                    <span>📄</span>
                    <span>{m.file.label} — دانلود فایل (CSV)</span>
                  </button>
                )}
              </div>
            ))}
            {status === "sending" && <p className="text-center text-sm text-slate-500">در حال ارسال…</p>}
            {status === "searching" && <p className="text-center text-sm text-slate-500">در حال جستجو در مرورگر شما…</p>}
            {activity.length > 0 && (
              <details className="rounded-xl border bg-slate-50 p-3 text-xs text-slate-600" open={busy}>
                <summary className="cursor-pointer font-bold">فعالیت دستیار ({activity.length})</summary>
                <ul className="mt-2 space-y-1" dir="ltr">
                  {activity.map((a, i) => (
                    <li key={i} className="font-mono break-words">{a}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
          {error && lastFailed && (
            <button className="mt-2 rounded border px-3 py-1 text-sm" onClick={() => send(lastFailed)}>
              تلاش مجدد
            </button>
          )}
          {agentId && (
            <form onSubmit={submit} className="mt-4 flex gap-2">
              <input
                className="flex-1 rounded-full border px-4 py-2"
                placeholder="چه داده‌ای استخراج کنم؟…"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                disabled={busy}
              />
              <button
                className="rounded-full bg-violet-700 px-5 py-2 text-white disabled:opacity-50"
                type="submit"
                disabled={busy}
              >
                ارسال
              </button>
            </form>
          )}
        </>
      )}
    </main>
  );
}
