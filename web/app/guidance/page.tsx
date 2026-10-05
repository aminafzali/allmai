"use client";

import { useEffect, useState } from "react";
import { api, type StreamEvent } from "@/lib/admin";
import { streamChatResolvingTools } from "@/lib/clientToolLoop";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

const KEY = "moshaver";
const CONV_KEY = "allmai_guidance_conv";

type Msg = { role: string; content: string; citations?: any[] };
type Status = "idle" | "sending" | "searching" | "streaming" | "error" | "done";
type Tab = "chat" | "personality" | "jobs";

type Question = { id: string; type: string; text: string };
type RiaType = { code: string; fa: string; en: string; desc: string; score: number };

export default function GuidanceApp() {
  const [ws, setWs] = useState("");
  const [tab, setTab] = useState<Tab>("chat");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [creating, setCreating] = useState(false);

  // chat
  const [convId, setConvId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Msg[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<string | null>(null);

  // personality
  const [quiz, setQuiz] = useState<Question[]>([]);
  const [scale, setScale] = useState<Array<{ v: number; label: string }>>([]);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [profile, setProfile] = useState<{ code: string | null; scores: Record<string, number>; types: RiaType[] } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // jobs
  const [codeInput, setCodeInput] = useState("");
  const [jobs, setJobs] = useState<any[]>([]);
  const [matching, setMatching] = useState(false);

  useEffect(() => {
    if (!ws) {
      setAgents([]);
      setAgentId("");
      setProfile(null);
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
    api(`/workspaces/${ws}/guidance/quiz`)
      .then((q) => {
        setQuiz(q.questions ?? []);
        setScale(q.scale ?? []);
      })
      .catch(() => {});
    api(`/workspaces/${ws}/guidance/profile`)
      .then((p) => {
        setProfile(p);
        if (p?.code) setCodeInput(p.code);
      })
      .catch(() => {});
  }, [ws]);

  async function createAgent() {
    if (!ws || creating) return;
    setCreating(true);
    setError("");
    try {
      const a = await api(`/workspaces/${ws}/agents`, {
        method: "POST",
        json: { key: KEY, type: "agent", name: "مشاور هدایت تحصیلی" },
      });
      setAgents((l) => [...l, a]);
      setAgentId(a.id);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setCreating(false);
    }
  }

  async function send(text: string) {
    if (!text.trim() || !agentId) return;
    setError("");
    setLastFailed(null);
    setStatus("sending");
    setHistory((h) => [...h, { role: "user", content: text }]);
    setMessage("");
    let acc = "";
    let cits: any[] = [];
    try {
      await streamChatResolvingTools(
        `/workspaces/${ws}/agents/${agentId}/chat/stream`,
        `/workspaces/${ws}/agents/${agentId}/chat/resume`,
        { message: text, kb_id: null, conversation_id: convId },
        (e: StreamEvent) => {
          if (e.type === "meta") {
            setConvId(e.conversation_id);
            localStorage.setItem(CONV_KEY, e.conversation_id);
            cits = e.citations ?? [];
          } else if (e.type === "searching" || e.type === "progress") {
            setStatus("searching");
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
      setHistory((h) => {
        const last = h[h.length - 1];
        return last?.role === "assistant-stream" ? h.slice(0, -1) : h;
      });
    }
  }

  async function submitQuiz() {
    if (!ws || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const r = await api(`/workspaces/${ws}/guidance/quiz`, {
        method: "POST",
        json: { answers },
      });
      setProfile({ code: r.code, scores: r.scores, types: r.types });
      setCodeInput(r.code);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setSubmitting(false);
    }
  }

  async function runMatch() {
    if (!ws || matching) return;
    setMatching(true);
    setError("");
    try {
      const r = await api(`/workspaces/${ws}/guidance/match`, {
        method: "POST",
        json: { code: codeInput || undefined, limit: 20 },
      });
      setJobs(r.jobs ?? []);
      if (r.code && r.code !== codeInput) setCodeInput(r.code);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setMatching(false);
    }
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
  const answered = Object.keys(answers).length;

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-xl font-bold">مشاور هدایت تحصیلی</h1>
      <p className="mt-1 text-xs text-slate-500">
        تیپ شخصیتی‌ات را بشناس، مشاغل مناسب را ببین و با مشاور گفتگو کن.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <WorkspaceSelect value={ws} onChange={setWs} />
        {ws && !agentId && (
          <button
            className="rounded bg-teal-700 px-3 py-1 text-sm text-white disabled:opacity-50"
            onClick={createAgent}
            disabled={creating}
          >
            {creating ? "…" : "ساخت مشاور"}
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
        {(["chat", "personality", "jobs"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-t border-x border-t px-4 py-2 ${tab === t ? "border-b-white bg-white font-bold" : "bg-slate-50 text-slate-500"}`}
          >
            {t === "chat" ? "گفتگو" : t === "personality" ? "تیپ شخصیتی شما" : "مشاغل مناسب شما"}
          </button>
        ))}
      </div>

      {tab === "chat" && (
        <>
          <div className="mt-4 space-y-3" aria-live="polite">
            {history.length === 0 && (
              <p className="text-center text-sm text-slate-400">
                مثلاً: «من به ریاضی علاقه دارم، چه رشته‌ای بخوانم؟»
              </p>
            )}
            {history.map((m, i) => (
              <div
                key={i}
                className={`rounded-2xl p-3 text-sm ${m.role === "user" ? "ml-12 bg-teal-700 text-white" : "mr-12 border bg-white"}`}
              >
                <p className="whitespace-pre-wrap">{m.content}{m.role === "assistant-stream" && <span className="animate-pulse"> ▍</span>}</p>
                {!!m.citations?.length && (
                  <p className="mt-2 text-[11px] opacity-70" dir="ltr">
                    {m.citations.map((c: any) => `[${c.n}] ${c.source ?? ""}`).join(" · ")}
                  </p>
                )}
              </div>
            ))}
            {status === "sending" && <p className="text-center text-sm text-slate-500">در حال ارسال…</p>}
            {status === "searching" && <p className="text-center text-sm text-slate-500">در حال جستجو…</p>}
          </div>
          {error && lastFailed && (
            <button className="mt-2 rounded border px-3 py-1 text-sm" onClick={() => send(lastFailed)}>
              تلاش مجدد
            </button>
          )}
          {agentId && (
            <form onSubmit={(e) => { e.preventDefault(); void send(message); }} className="mt-4 flex gap-2">
              <input
                className="flex-1 rounded-full border px-4 py-2"
                placeholder="سؤالت را بپرس…"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                disabled={busy}
              />
              <button
                className="rounded-full bg-teal-700 px-5 py-2 text-white disabled:opacity-50"
                type="submit"
                disabled={busy}
              >
                ارسال
              </button>
            </form>
          )}
        </>
      )}

      {tab === "personality" && (
        <section className="mt-4">
          {profile?.code ? (
            <div className="rounded border bg-white p-4">
              <p className="text-sm text-slate-500">تیپ شخصیتی شما</p>
              <p className="mt-1 text-3xl font-extrabold" dir="ltr">{profile.code}</p>
              <div className="mt-3 space-y-2">
                {profile.types.slice(0, 3).map((t) => (
                  <div key={t.code} className="rounded border p-2 text-sm">
                    <strong>{t.fa}</strong> <span className="text-xs text-slate-400" dir="ltr">{t.code} · {t.en}</span>
                    <span className="float-left rounded bg-teal-100 px-2 text-xs">امتیاز {t.score}</span>
                    <p className="mt-1 text-xs text-slate-600">{t.desc}</p>
                  </div>
                ))}
              </div>
              <button
                className="mt-3 rounded border px-3 py-1 text-sm"
                onClick={() => { setProfile(null); setAnswers({}); }}
              >
                آزمون مجدد
              </button>
            </div>
          ) : (
            <div className="rounded border bg-white p-4">
              <p className="text-sm font-bold">آزمون تیپ شخصیتی (۱۸ سؤال — حدود ۳ دقیقه)</p>
              <p className="mt-1 text-xs text-slate-500">
                به هر جمله از ۱ (کاملاً مخالفم) تا ۵ (کاملاً موافقم) نمره بده. حداقل ۱۲ سؤال لازم است.
              </p>
              <div className="mt-3 space-y-3">
                {quiz.map((q, i) => (
                  <div key={q.id} className="rounded border p-2 text-sm">
                    <p>{i + 1}. {q.text}</p>
                    <div className="mt-1 flex gap-1" dir="ltr">
                      {[1, 2, 3, 4, 5].map((v) => (
                        <button
                          key={v}
                          type="button"
                          title={scale.find((s) => s.v === v)?.label ?? String(v)}
                          onClick={() => setAnswers((a) => ({ ...a, [q.id]: v }))}
                          className={`h-8 w-8 rounded-full border text-sm ${answers[q.id] === v ? "bg-teal-700 text-white" : "bg-slate-50"}`}
                        >
                          {v}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <button
                className="mt-3 rounded bg-teal-700 px-4 py-2 text-sm text-white disabled:opacity-50"
                onClick={submitQuiz}
                disabled={submitting || answered < 12}
              >
                {submitting ? "…" : `مشاهده نتیجه (${answered} پاسخ)`}
              </button>
            </div>
          )}
        </section>
      )}

      {tab === "jobs" && (
        <section className="mt-4">
          <div className="flex flex-wrap items-center gap-2 rounded border bg-white p-3">
            <span className="text-sm text-slate-500">تیپ RIASEC:</span>
            <input
              className="w-24 rounded border px-2 py-1 font-mono text-sm"
              dir="ltr"
              maxLength={3}
              value={codeInput}
              onChange={(e) => setCodeInput(e.target.value.toUpperCase().replace(/[^RIASEC]/g, ""))}
              placeholder={profile?.code ?? "SIA"}
            />
            <button
              className="rounded bg-teal-700 px-4 py-1 text-sm text-white disabled:opacity-50"
              onClick={runMatch}
              disabled={matching || !ws}
            >
              {matching ? "…" : "پیدا کردن مشاغل"}
            </button>
            {!profile?.code && (
              <span className="text-xs text-slate-400">اول در تب «تیپ شخصیتی» آزمون بده تا کدت خودکار بیاید.</span>
            )}
          </div>
          {jobs.length > 0 && (
            <div className="mt-3 space-y-2">
              {jobs.map((j, i) => (
                <details key={i} className="rounded border bg-white p-3 text-sm">
                  <summary className="cursor-pointer">
                    <strong>{i + 1}. {j.title}</strong>
                    <span className="mr-2 rounded bg-teal-100 px-2 text-xs">تطابق {j.matched}/3</span>
                    <span className="mr-2 font-mono text-xs text-slate-400" dir="ltr">{j.riasec}</span>
                  </summary>
                  <div className="mt-2 space-y-1 text-xs text-slate-600">
                    {j.group && <p><strong>گروه:</strong> {j.group}</p>}
                    {j.education && <p><strong>تحصیلات معمول:</strong> {j.education}</p>}
                    {j.readiness && <p><strong>سطح آمادگی:</strong> {j.readiness}</p>}
                    {j.interests && <p><strong>علایق:</strong> {j.interests}</p>}
                    {j.description && <p><strong>شرح:</strong> {j.description}</p>}
                    {j.source && <p className="text-slate-400">منبع: {j.source}</p>}
                  </div>
                </details>
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
