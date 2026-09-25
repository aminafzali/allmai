"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

export default function AgentsPage() {
  const [ws, setWs] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [key, setKey] = useState("teacher_lesson_planner");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [testId, setTestId] = useState("");
  const [kb, setKb] = useState("");
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setItems).catch((e: Error) => setError(String(e)));
    else setItems([]);
  };
  useEffect(reload, [ws]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const a = await api(`/workspaces/${ws}/agents`, {
        method: "POST",
        json: { key, type: "agent", name: name || key },
      });
      setName("");
      setTestId(a.id);
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  async function testChat(e: React.FormEvent) {
    e.preventDefault();
    if (!testId) return;
    setError("");
    setBusy(true);
    try {
      const r = await api(`/workspaces/${ws}/agents/${testId}/chat`, {
        method: "POST",
        json: { message, kb_id: kb || null },
      });
      setReply(r);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">ایجنت‌ها</h1>
      <p className="mt-1 text-sm text-slate-500">
        نمونه‌های ورک‌اسپیس. مدیریت تعریف‌ها و دانش سراسری در{" "}
        <a className="text-blue-700 hover:underline" href="/admin/agent-studio">
          استودیو ایجنت
        </a>
        .
      </p>
      <div className="mt-4">
        <WorkspaceSelect value={ws} onChange={setWs} />
      </div>
      {ws && (
        <form onSubmit={create} className="mt-4 flex flex-wrap gap-2">
          <select className="rounded border px-2 py-1" value={key} onChange={(e) => setKey(e.target.value)}>
            <option value="teacher_lesson_planner">teacher_lesson_planner</option>
            <option value="student_academic_coach">student_academic_coach</option>
          </select>
          <input
            className="rounded border px-3 py-1"
            placeholder="نام (اختیاری)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit">
            بساز
          </button>
        </form>
      )}
      <FormError e={error} />
      <ul className="mt-4 space-y-2">
        {items.map((a) => (
          <li key={a.id} className="rounded border bg-white p-3 text-sm">
            <strong>{a.name || a.key}</strong> <span className="text-slate-500">({a.key} · {a.type})</span>
            {a.definition_id && (
              <span className="mr-2 rounded bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700">definition-linked</span>
            )}
            {a.owner_user_id ? (
              <span className="mr-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-700">personal</span>
            ) : (
              <span className="mr-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">shared</span>
            )}
            {a.custom_instructions && (
              <p className="mt-1 line-clamp-2 text-slate-600">{a.custom_instructions}</p>
            )}
            <div className="mt-1">
              <button className="text-blue-700 hover:underline" onClick={() => setTestId(a.id)}>
                تست این ایجنت
              </button>
            </div>
          </li>
        ))}
      </ul>

      {testId && (
        <section className="mt-6 rounded border bg-white p-4">
          <h2 className="font-bold">تست ایجنت</h2>
          <div className="mt-2">
            <KBSelect wsId={ws} value={kb} onChange={setKb} />
          </div>
          <form onSubmit={testChat} className="mt-2 flex gap-2">
            <input
              className="flex-1 rounded border px-3 py-1"
              placeholder="پیام تست…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
            <button className="rounded bg-green-700 px-4 py-1 text-white" type="submit" disabled={busy}>
              {busy ? "…" : "ارسال"}
            </button>
          </form>
          {reply && (
            <div className="mt-3 text-sm">
              <p className="whitespace-pre-wrap rounded bg-slate-100 p-3">{reply.answer}</p>
              {reply.citations?.length > 0 && (
                <ul className="mt-2 list-disc pr-5 text-xs text-slate-600">
                  {reply.citations.map((c: any) => (
                    <li key={c.n} dir="ltr">
                      [{c.n}] {c.source} {c.page_no ? `p.${c.page_no}` : ""} ({c.chunk_id.slice(0, 8)})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
