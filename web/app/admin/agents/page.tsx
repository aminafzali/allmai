"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

export default function AgentsPage() {
  const [ws, setWs] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [defs, setDefs] = useState<any[]>([]);
  const [defId, setDefId] = useState("");
  const [customKey, setCustomKey] = useState("");
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
  useEffect(() => {
    api("/admin/agent-definitions")
      .then((ds: any[]) => {
        setDefs(ds);
        const firstActive = ds.find((d) => d.is_active) ?? ds[0];
        if (firstActive) setDefId(firstActive.id);
      })
      .catch((e: Error) => setError(String(e)));
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const def = defs.find((d) => d.id === defId);
      // تعریف استودیو → نمونه لینک‌شده (definition_id)؛ کلید دستی → لینک خودکار از روی key در بک‌اند
      const body = def
        ? { key: def.key, type: def.type || "agent", name: name || def.title || def.key, definition_id: def.id }
        : { key: customKey.trim(), type: "agent", name: name || customKey.trim() };
      const a = await api(`/workspaces/${ws}/agents`, { method: "POST", json: body });
      setName("");
      setCustomKey("");
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
        <form onSubmit={create} className="mt-4 flex flex-wrap items-center gap-2 rounded border bg-white p-3">
          <label className="text-sm text-slate-600">
            تعریف استودیو
            <select
              className="mr-2 rounded border px-2 py-1"
              value={defId}
              onChange={(e) => setDefId(e.target.value)}
            >
              <option value="">کلید دستی…</option>
              {defs.map((d: any) => (
                <option key={d.id} value={d.id} disabled={!d.is_active}>
                  {d.title || d.key} ({d.key}){d.is_active ? "" : " — غیرفعال"}
                </option>
              ))}
            </select>
          </label>
          {defId === "" && (
            <input
              className="rounded border px-3 py-1 font-mono"
              dir="ltr"
              placeholder="custom_key"
              value={customKey}
              onChange={(e) => setCustomKey(e.target.value)}
            />
          )}
          <input
            className="rounded border px-3 py-1"
            placeholder="نام نمونه (اختیاری)"
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
        {items.map((a) => {
          const linked = defs.find((d: any) => d.id === a.definition_id);
          return (
          <li key={a.id} className="rounded border bg-white p-3 text-sm">
            <strong>{a.name || a.key}</strong> <span className="text-slate-500">({a.key} · {a.type})</span>
            {a.definition_id && (
              <span className="mr-2 rounded bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700">
                {linked ? `متصل به «${linked.title || linked.key}»` : "definition-linked"}
              </span>
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
          );
        })}
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
