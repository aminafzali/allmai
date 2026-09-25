"use client";

import { useEffect, useState } from "react";
import { api, streamChat, type StreamEvent } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

type Msg = { role: string; content: string; citations?: any[] };
type Status = "idle" | "sending" | "streaming" | "error" | "done";

export default function TeacherChat() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Msg[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<string | null>(null);

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  async function send(text: string) {
    if (!text.trim() || !agentId) return;
    setError("");
    setLastFailed(null);
    setStatus("sending");
    setHistory((h) => [...h, { role: "user", content: text }]);
    setMessage("");
    let acc = "";
    let cits: any[] = [];
    let cid: string | null = convId;
    try {
      await streamChat(
        `/workspaces/${ws}/agents/${agentId}/chat/stream`,
        { message: text, kb_id: kb || null, conversation_id: convId },
        (e: StreamEvent) => {
          if (e.type === "meta") {
            cid = e.conversation_id;
            setConvId(e.conversation_id);
            cits = e.citations ?? [];
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
            cits = e.citations ?? cits;
            const final = acc;
            setHistory((h) => {
              const last = h[h.length - 1];
              const done: Msg = { role: "assistant", content: final, citations: cits };
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
      // drop the partial stream bubble on failure
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

  function retry() {
    if (lastFailed) void send(lastFailed);
  }

  function newChat() {
    setConvId(null);
    setHistory([]);
    setStatus("idle");
    setError("");
    setLastFailed(null);
  }

  return (
    <main>
      <h1 className="text-xl font-bold">گفتگو با دستیار</h1>
      <div className="mt-4 flex flex-wrap gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
        <KBSelect wsId={ws} value={kb} onChange={setKb} />
        <select className="rounded border px-2 py-1" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
          <option value="">— ایجنت —</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>{a.name || a.key}</option>
          ))}
        </select>
        {convId && (
          <button className="rounded border px-3 py-1 text-sm" onClick={newChat}>
            گفتگوی جدید
          </button>
        )}
      </div>
      <FormError e={error} />
      {error && lastFailed && (
        <button className="mt-2 rounded border px-3 py-1 text-sm" onClick={retry}>
          تلاش مجدد
        </button>
      )}
      <div className="mt-4 space-y-2" aria-live="polite">
        {history.length === 0 && (
          <p className="text-sm text-slate-500">هنوز پیامی نیست — سؤال خود را بپرسید.</p>
        )}
        {history.map((m, i) => (
          <div key={i} className={`rounded p-3 text-sm ${m.role === "user" ? "bg-blue-50" : "border bg-white"}`}>
            <strong className="text-xs text-slate-500">
              {m.role === "user" ? "شما" : "دستیار"}
              {m.role === "assistant-stream" && <span className="animate-pulse"> ▍</span>}
            </strong>
            <p className="mt-1 whitespace-pre-wrap">{m.content}</p>
            {m.citations && m.citations.length > 0 && (
              <ul className="mt-2 list-disc pr-5 text-xs text-slate-600">
                {m.citations.map((c: any) => (
                  <li key={c.n} dir="ltr">
                    [{c.n}] {c.source} {c.page_no ? `p.${c.page_no}` : ""}
                    {c.start_ms != null ? ` ${c.start_ms}-${c.end_ms}ms` : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {status === "sending" && <p className="text-sm text-slate-500">در حال ارسال…</p>}
      </div>
      {agentId && (
        <form onSubmit={submit} className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border px-3 py-2"
            placeholder="پیام…"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={status === "sending" || status === "streaming"}
          />
          <button
            className="rounded bg-green-700 px-4 py-2 text-white disabled:opacity-50"
            type="submit"
            disabled={status === "sending" || status === "streaming"}
          >
            ارسال
          </button>
        </form>
      )}
    </main>
  );
}
