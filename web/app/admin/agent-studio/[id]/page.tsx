"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

const TABS = [
  { id: "general", label: "عمومی" },
  { id: "behavior", label: "رفتار" },
  { id: "capabilities", label: "قابلیت‌ها" },
  { id: "tools", label: "ابزارها" },
  { id: "workflow", label: "گردش‌کار" },
  { id: "model", label: "مدل" },
  { id: "safety", label: "ایمنی" },
  { id: "output", label: "قالب خروجی" },
  { id: "knowledge", label: "دانش سراسری" },
  { id: "instances", label: "نمونه‌ها" },
] as const;

function JsonArea({ value, onChange }: { value: any; onChange: (v: any) => void }) {
  const [txt, setTxt] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [err, setErr] = useState("");
  useEffect(() => setTxt(JSON.stringify(value ?? {}, null, 2)), [value?.id ?? value]);
  return (
    <div>
      <textarea
        className="w-full rounded border px-3 py-1 font-mono text-xs"
        rows={8}
        dir="ltr"
        value={txt}
        onChange={(e) => {
          setTxt(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setErr("");
          } catch {
            setErr("JSON معتبر نیست");
          }
        }}
      />
      {err && <p className="text-xs text-red-600">{err}</p>}
    </div>
  );
}

export default function DefinitionDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [tab, setTab] = useState<string>("general");
  const [def, setDef] = useState<any>(null);
  const [draft, setDraft] = useState<any>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [allGlobal, setAllGlobal] = useState<any[]>([]);
  const [assigned, setAssigned] = useState<string[]>([]);
  const [instances, setInstances] = useState<any[]>([]);

  const reload = () => {
    api(`/admin/agent-definitions/${id}`).then((d) => {
      setDef(d);
      setDraft(d);
    }).catch((e: Error) => setError(String(e)));
    api(`/admin/agent-definitions/${id}/knowledge`).then((r) => setAssigned(r.kb_ids ?? [])).catch(() => {});
    api(`/admin/agent-definitions/${id}/instances`).then(setInstances).catch(() => {});
    api("/admin/global-knowledge-bases").then(setAllGlobal).catch(() => {});
  };
  useEffect(reload, [id]);

  async function save() {
    setError("");
    setSaved("");
    try {
      const d = await api(`/admin/agent-definitions/${id}`, { method: "PUT", json: draft });
      setDef(d);
      setDraft(d);
      setSaved("ذخیره شد");
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  async function toggleKb(kbId: string) {
    setError("");
    const next = assigned.includes(kbId) ? assigned.filter((k) => k !== kbId) : [...assigned, kbId];
    try {
      const r = await api(`/admin/agent-definitions/${id}/knowledge`, {
        method: "PUT",
        json: { kb_ids: next },
      });
      setAssigned(r.kb_ids ?? next);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  const set = (k: string) => (e: any) =>
    setDraft((d: any) => ({ ...d, [k]: e?.target ? e.target.value : e }));

  if (!def) return <main><p className="text-sm text-slate-500">در حال بارگذاری…</p><FormError e={error} /></main>;

  return (
    <main>
      <h1 className="text-xl font-bold">{def.title || def.key}</h1>
      <p className="text-sm text-slate-500" dir="ltr">{def.key}</p>

      <div className="mt-4 flex flex-wrap gap-1 border-b">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-t px-3 py-1.5 text-sm ${tab === t.id ? "bg-white font-bold text-blue-700 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <section className="rounded-b border border-t-0 bg-white p-4">
        {tab === "general" && (
          <div className="space-y-2">
            <label className="block text-sm">عنوان<input className="mt-1 w-full rounded border px-3 py-1" value={draft.title ?? ""} onChange={set("title")} /></label>
            <label className="block text-sm">توضیحات<textarea className="mt-1 w-full rounded border px-3 py-1" rows={2} value={draft.description ?? ""} onChange={set("description")} /></label>
            <label className="block text-sm">دستورالعمل<textarea className="mt-1 w-full rounded border px-3 py-1" rows={6} value={draft.instructions ?? ""} onChange={set("instructions")} /></label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!draft.is_active} onChange={(e) => setDraft((d: any) => ({ ...d, is_active: e.target.checked }))} />
              فعال
            </label>
          </div>
        )}
        {tab === "behavior" && (
          <div className="space-y-3">
            <div><h3 className="mb-1 text-sm font-bold">قوانین رفتاری (JSON)</h3><JsonArea value={draft.behavior_rules} onChange={(v) => setDraft((d: any) => ({ ...d, behavior_rules: v }))} /></div>
            <label className="block text-sm">روش‌شناسی<textarea className="mt-1 w-full rounded border px-3 py-1" rows={5} value={draft.methodology ?? ""} onChange={set("methodology")} /></label>
          </div>
        )}
        {tab === "capabilities" && (<JsonArea value={draft.capabilities} onChange={(v) => setDraft((d: any) => ({ ...d, capabilities: v }))} />)}
        {tab === "tools" && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">ابزارهای مجاز این تعریف (مثل knowledge_search و memory_search)</p>
            <JsonArea value={draft.tools} onChange={(v) => setDraft((d: any) => ({ ...d, tools: v }))} />
          </div>
        )}
        {tab === "workflow" && (<JsonArea value={draft.workflow} onChange={(v) => setDraft((d: any) => ({ ...d, workflow: v }))} />)}
        {tab === "model" && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">پیش‌فرض مدل این تعریف؛ خالی یعنی تنظیم سراسری چت.</p>
            <JsonArea value={draft.model_defaults} onChange={(v) => setDraft((d: any) => ({ ...d, model_defaults: v }))} />
          </div>
        )}
        {tab === "safety" && (<JsonArea value={draft.safety_rules} onChange={(v) => setDraft((d: any) => ({ ...d, safety_rules: v }))} />)}
        {tab === "output" && (<JsonArea value={draft.output_format} onChange={(v) => setDraft((d: any) => ({ ...d, output_format: v }))} />)}

        {tab === "knowledge" && (
          <div>
            <p className="mb-2 text-xs text-slate-500">
              فقط پایگاه‌های <strong>سراسری</strong> قابل انتساب‌اند. نمونه‌ها این دانش را فقط به‌صورت خواندنی به ارث می‌برند.
            </p>
            <ul className="space-y-2">
              {allGlobal.map((k) => (
                <li key={k.id} className="flex items-center justify-between rounded border p-2 text-sm">
                  <span>{k.title} <span className="text-xs text-slate-400" dir="ltr">{String(k.id).slice(0, 8)}</span></span>
                  <button
                    onClick={() => toggleKb(k.id)}
                    className={`rounded px-3 py-1 text-white ${assigned.includes(k.id) ? "bg-red-600" : "bg-green-700"}`}
                  >
                    {assigned.includes(k.id) ? "حذف انتساب" : "انتساب"}
                  </button>
                </li>
              ))}
              {allGlobal.length === 0 && <p className="text-sm text-slate-400">پایگاه سراسری وجود ندارد.</p>}
            </ul>
          </div>
        )}

        {tab === "instances" && (
          <div>
            <p className="mb-2 text-xs text-slate-500">
              نمونه‌های این تعریف در ورک‌اسپیس‌ها. دانش سراسری در نمونه فقط نمایشی است؛ مدیریت دانش هر نمونه در صفحه ایجنت همان ورک‌اسپیس انجام می‌شود.
            </p>
            <ul className="space-y-2">
              {instances.map((i) => (
                <li key={i.id} className="rounded border p-2 text-sm" dir="ltr">
                  {i.name || i.key} · ws {String(i.workspace_id).slice(0, 8)}
                  {i.owner_user_id ? ` · personal ${String(i.owner_user_id).slice(0, 8)}` : " · shared"}
                </li>
              ))}
              {instances.length === 0 && <p className="text-sm text-slate-400">نمونه‌ای وجود ندارد.</p>}
            </ul>
          </div>
        )}

        {tab !== "knowledge" && tab !== "instances" && (
          <button onClick={save} className="mt-3 rounded bg-blue-700 px-4 py-1 text-white">
            ذخیره
          </button>
        )}
        {saved && <span className="mr-2 text-sm text-green-700">{saved}</span>}
        <FormError e={error} />
      </section>
    </main>
  );
}
