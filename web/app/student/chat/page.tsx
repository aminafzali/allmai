"use client";

import { useEffect, useState } from "react";
import { api, streamChat, type StreamEvent } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

const CONV_KEY = "allmai_coach_conv";

type Msg = { role: string; content: string };
type Status = "idle" | "sending" | "streaming" | "error" | "done";

export default function CoachChat() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Msg[]>([]);
  const [hasPlan, setHasPlan] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<string | null>(null);

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
    setConvId(localStorage.getItem(CONV_KEY));
  }, [ws]);

  useEffect(() => {
    const coach = agents.find((a) => a.key === "student_academic_coach");
    if (coach) setAgentId(coach.id);
  }, [agents]);

  async function send(text: string) {
    if (!text.trim() || !agentId) return;
    setError("");
    setLastFailed(null);
    setStatus("sending");
    setHistory((h) => [...h, { role: "user", content: text }]);
    setMessage("");
    let acc = "";
    try {
      await streamChat(
        `/workspaces/${ws}/agents/${agentId}/coach/chat/stream`,
        { message: text, kb_id: kb || null, conversation_id: convId },
        (e: StreamEvent) => {
          if (e.type === "meta") {
            setConvId(e.conversation_id);
            localStorage.setItem(CONV_KEY, e.conversation_id);
            if (e.has_plan !== undefined) setHasPlan(e.has_plan);
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
            setConvId(e.conversation_id);
            localStorage.setItem(CONV_KEY, e.conversation_id);
            if (e.has_plan !== undefined) setHasPlan(e.has_plan);
            const final = acc;
            setHistory((h) => {
              const last = h[h.length - 1];
              const done: Msg = { role: "assistant", content: final };
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

  return (
    <main>
      <h1 className="text-xl font-bold">مربی تحصیلی</h1>
      <div className="mt-4 flex flex-wrap gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
        <KBSelect wsId={ws} value={kb} onChange={setKb} />
        {convId && (
          <button className="rounded border px-3 py-1 text-sm" onClick={newChat}>
            گفتگوی جدید
          </button>
        )}
        {hasPlan && (
          <a className="rounded border px-3 py-1 text-sm text-blue-700" href="/student/plan">
            مشاهده برنامه
          </a>
        )}
      </div>
      <FormError e={error} />
      {error && lastFailed && (
        <button className="mt-2 rounded border px-3 py-1 text-sm" onClick={() => send(lastFailed)}>
          تلاش مجدد
        </button>
      )}
      <div className="mt-4 space-y-2" aria-live="polite">
        {history.length === 0 && (
          <p className="text-sm text-slate-500">سلام! برنامه‌ات را بگو تا با هم جلو برویم.</p>
        )}
        {history.map((m, i) => (
          <div key={i} className={`rounded p-3 text-sm ${m.role === "user" ? "bg-green-50" : "border bg-white"}`}>
            <strong className="text-xs text-slate-500">
              {m.role === "user" ? "شما" : "مربی"}
              {m.role === "assistant-stream" && <span className="animate-pulse"> ▍</span>}
            </strong>
            <p className="mt-1 whitespace-pre-wrap">{m.content}</p>
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
