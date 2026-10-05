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
  { id: "prompts", label: "پرامپت‌ها" },
  { id: "safety", label: "ایمنی" },
  { id: "output", label: "قالب خروجی" },
  { id: "knowledge", label: "دانش سراسری" },
  { id: "instances", label: "نمونه‌ها" },
] as const;

// راهنمای فارسی هر تب: تب فعال چه چیزی را تنظیم می‌کند.
const TAB_GUIDES: Record<string, string> = {
  general: "مشخصات پایه: عنوان، توضیح، دستورالعمل اصلی، نوع (agent حالت‌مند و ابزار‌دار / assistant ساده) و فعال یا غیرفعال بودن.",
  behavior: "قوانین رفتاری (JSON) و روش‌شناسی: سبک و چارچوب پاسخ‌گویی ایجنت.",
  capabilities: "قابلیت‌های اعلامی ایجنت (JSON) — برای مستندسازی و نمایش در UI.",
  tools: "ابزارهای مجاز این تعریف: کدام جستجوی دانش/حافظه/اکسل در اختیار ایجنت باشد. تیک نخورده = آن قابلیت در پاسخ استفاده نمی‌شود.",
  workflow: "گردش‌کار سفارشی (JSON). خالی یعنی همان مسیر خطی استاندارد (حافظه ← بازیابی ← پرامپت ← مدل).",
  model: "مدل و پارامترهای همین تعریف. هر فیلد خالی یعنی ارث‌بری از تنظیم ایجنت و سپس سراسری (جدول بالا مقدار مؤثر را نشان می‌دهد).",
  prompts: "متن قالب‌های پاسخ این ایجنت. فقط قالب‌های مرتبط با همین ایجنت نشان داده می‌شود؛ خالی یعنی پیش‌فرض داخلی موتور.",
  safety: "قوانین ایمنی (JSON): محدودیت‌های محتوایی و رفتاری.",
  output: "قالب خروجی (JSON): ساختار موردانتظار پاسخ.",
  knowledge: "پایگاه‌های دانش سراسری منتسب به این تعریف. نمونه‌ها این دانش را فقط به‌صورت خواندنی به ارث می‌برند.",
  instances: "نمونه‌های ساخته‌شده از این تعریف در ورک‌اسپیس‌ها (فقط نمایش).",
};

// کدام پرامپت برای کدام ایجنت معنا دارد. ایجنت سفارشی/ناشناخته → همه (با راهنما).
const PROMPTS_FOR_KEY: Record<string, string[]> = {
  teacher_lesson_planner: ["lesson_plan"],
  student_academic_coach: ["study_plan", "coach_chat"],
  note_taking_assistant: ["note_chat"],
};

const PROMPT_FA: Record<string, { title: string; guide: string }> = {
  lesson_plan: { title: "طرح درس", guide: "فقط ایجنت معلم: قالب تولید طرح درس (بخش‌ها، اهداف، فعالیت‌ها) از روی زمینه بازیابی‌شده." },
  study_plan: { title: "برنامه مطالعاتی", guide: "فقط مربی دانش‌آموز: قالب ساخت برنامه هفتگی از پروفایل و اهداف." },
  coach_chat: { title: "گفتگوی مربی", guide: "فقط مربی دانش‌آموز: لحن و چارچوب هر جواب گفتگو (ابزارهای برنامه به‌صورت خودکار افزوده می‌شوند)." },
  note_chat: { title: "گفتگوی دستیار یادداشت", guide: "فقط دستیار یادداشت: لحن پاسخ از روی فایل‌ها، یادداشت‌ها و لینک‌ها." },
};

// Curated model ids verified against the provider /models list. All are
// routed through the same server-side OpenAI-compatible endpoint + key.
const MODEL_OPTIONS = [
  { group: "Gemini", id: "gemini-2.5-flash" },
  { group: "Gemini", id: "gemini-2.5-flash-lite" },
  { group: "Gemini", id: "gemini-3-flash-preview" },
  { group: "Gemini", id: "gemini-2.5-pro" },
  { group: "OpenAI", id: "gpt-4o-mini" },
  { group: "OpenAI", id: "gpt-4.1-mini" },
  { group: "OpenAI", id: "gpt-4.1-nano" },
  { group: "OpenAI", id: "gpt-4o" },
  { group: "ارزان", id: "deepseek-v4.1-flash" },
];

const KNOWN_TOOLS = [
  { id: "knowledge_search", label: "جستجوی دانش ورک‌اسپیس", desc: "بازیابی چانک‌های پایگاه‌های منتسب نمونه (فایل، یادداشت، لینک، ویدئو)" },
  { id: "global_knowledge_search", label: "جستجوی دانش سراسری", desc: "بازیابی از پایگاه‌های سراسری منتسب به این تعریف (فقط خواندنی)" },
  { id: "web_search", label: "جستجوی اینترنتی", desc: "پاسخ زمینه‌دار با جستجوی وب سروری (درگاه فعلی) + لینک منابع" },
  { id: "maps_search", label: "جستجوی مکان (Maps)", desc: "نام/آدرس/امتیاز/تلفن کسب‌وکارها از Google Maps — فقط سؤال‌های مکانی" },
  { id: "memory_search", label: "جستجوی حافظه", desc: "یادآوری خاطرات مرتبط کاربر" },
  { id: "memory_facts", label: "حقایق کاربر", desc: "پروفایل و حقایق ذخیره‌شده در پرامپت" },
  { id: "excel_query", label: "پرسش اکسل/CSV", desc: "محاسبات SUM/COUNT/AVG/GROUP BY روی شیت‌ها با DuckDB (سؤال عددی)" },
];

const TOKEN_PRESETS = [500, 1000, 1500, 3000, 6000];

function FieldClear({ onClear }: { onClear: () => void }) {
  return (
    <button onClick={onClear} title="حذف override (بازگشت به ارث‌بری)"
      className="rounded border px-2 py-0.5 text-xs text-slate-500 hover:text-red-600">
      × ارث‌بری
    </button>
  );
}

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

function ModelTab({ draft, setDraft, eff }: {
  draft: any; setDraft: (f: (d: any) => any) => void; eff: any | null;
}) {
  const md: Record<string, any> = draft.model_defaults ?? {};
  const set = (k: string, v: any) =>
    setDraft((d: any) => ({ ...d, model_defaults: { ...(d.model_defaults ?? {}), [k]: v } }));
  const clear = (k: string) =>
    setDraft((d: any) => {
      const next = { ...(d.model_defaults ?? {}) };
      delete next[k];
      return { ...d, model_defaults: next };
    });
  const modelVal: string = md.model ?? "";
  const knownIds = MODEL_OPTIONS.map((o) => o.id);
  const customModel = modelVal !== "" && !knownIds.includes(modelVal);

  const effRow = (label: string, key: string) => (
    <div className="flex items-center justify-between border-b py-1 text-sm last:border-0">
      <span className="text-slate-500">{label}</span>
      <span>
        <strong className="font-mono" dir="ltr">{String(eff?.[key] ?? "—")}</strong>{" "}
        <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500" dir="ltr">
          {eff?.sources?.[key] ?? ""}
        </span>
      </span>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="rounded border bg-slate-50 p-3">
        <h3 className="mb-1 text-sm font-bold">پیکربندی مؤثر (همین حالا در پاسخ‌ها استفاده می‌شود)</h3>
        {eff ? (
          <div>
            {effRow("پرووایدر", "provider")}
            {effRow("مدل", "model")}
            {effRow("دما (temperature)", "temperature")}
            {effRow("سقف توکن هر جواب", "max_tokens")}
          </div>
        ) : (
          <p className="text-xs text-slate-400">در حال بارگذاری…</p>
        )}
      </div>

      <div>
        <h3 className="mb-1 text-sm font-bold">Override این تعریف <span className="font-normal text-slate-400">(خالی = ارث‌بری از تنظیم ایجنت/سراسری)</span></h3>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-32 text-slate-500">پرووایدر</span>
            <select
              className="rounded border px-2 py-1"
              value={md.provider ?? ""}
              onChange={(e) => (e.target.value === "" ? clear("provider") : set("provider", e.target.value))}
            >
              <option value="">ارث‌بری</option>
              <option value="openai_compat">openai_compat (درگاه سازگار)</option>
              <option value="gemini">gemini (مستقیم گوگل)</option>
            </select>
            {md.provider !== undefined && <FieldClear onClear={() => clear("provider")} />}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-32 text-slate-500">مدل</span>
            <select
              className="rounded border px-2 py-1 font-mono"
              dir="ltr"
              value={customModel ? "__custom" : modelVal}
              onChange={(e) => {
                if (e.target.value === "") clear("model");
                else if (e.target.value !== "__custom") set("model", e.target.value);
              }}
            >
              <option value="">ارث‌بری</option>
              {MODEL_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>{o.group} — {o.id}</option>
              ))}
              <option value="__custom">مدل دلخواه…</option>
            </select>
            {(customModel || modelVal !== "") && modelVal !== "" && (
              <input
                className="rounded border px-2 py-1 font-mono"
                dir="ltr"
                placeholder="model id"
                value={customModel ? modelVal : ""}
                onChange={(e) => set("model", e.target.value)}
              />
            )}
            {modelVal !== "" && <FieldClear onClear={() => clear("model")} />}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-32 text-slate-500">دما: <strong dir="ltr">{md.temperature ?? "ارث‌بری"}</strong></span>
            <input
              type="range" min={0} max={2} step={0.1}
              className="w-48"
              value={Number(md.temperature ?? 0.7)}
              onChange={(e) => set("temperature", Number(e.target.value))}
            />
            {md.temperature !== undefined && <FieldClear onClear={() => clear("temperature")} />}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-32 text-slate-500">سقف توکن هر جواب</span>
            <input
              type="number" min={100} max={16000} step={100}
              className="w-28 rounded border px-2 py-1 font-mono"
              dir="ltr"
              placeholder="ارث‌بری"
              value={md.max_tokens ?? ""}
              onChange={(e) => (e.target.value === "" ? clear("max_tokens") : set("max_tokens", Number(e.target.value)))}
            />
            <span className="flex gap-1">
              {TOKEN_PRESETS.map((p) => (
                <button key={p} onClick={() => set("max_tokens", p)}
                  className={`rounded border px-2 py-0.5 font-mono text-xs ${md.max_tokens === p ? "bg-blue-700 text-white" : "text-slate-600"}`}>
                  {p}
                </button>
              ))}
            </span>
            {md.max_tokens !== undefined && <FieldClear onClear={() => clear("max_tokens")} />}
          </div>
        </div>
      </div>
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
  const [eff, setEff] = useState<any | null>(null);
  const [promptDocs, setPromptDocs] = useState<any[]>([]);
  const [showDefault, setShowDefault] = useState<string | null>(null);

  const reload = () => {
    api(`/admin/agent-definitions/${id}`).then((d) => {
      setDef(d);
      setDraft(d);
    }).catch((e: Error) => setError(String(e)));
    api(`/admin/agent-definitions/${id}/effective-config`).then(setEff).catch(() => setEff(null));
    api("/admin/agent-definitions/prompts/defaults").then((r) => setPromptDocs(r.templates ?? [])).catch(() => {});
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
      api(`/admin/agent-definitions/${id}/effective-config`).then(setEff).catch(() => {});
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

      <div className="mt-4 flex flex-wrap gap-1 border-b" role="tablist" aria-label="بخش‌های تعریف ایجنت">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={active}
              title={TAB_GUIDES[t.id]}
              onClick={() => setTab(t.id)}
              className={`rounded-t-md border border-b-0 px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "border-slate-300 bg-white font-bold text-blue-700 shadow-[inset_0_2px_0_0_#1d4ed8]"
                  : "border-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-800"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <p className="rounded-b border border-t-0 bg-blue-50/60 px-3 py-1.5 text-xs leading-5 text-slate-600">
        <strong className="text-blue-800">این تب چه کار می‌کند؟ </strong>
        {TAB_GUIDES[tab]}
      </p>

      <section className="rounded-b border border-t-0 bg-white p-4">
        {tab === "general" && (
          <div className="space-y-2">
            <label className="block text-sm">عنوان<input className="mt-1 w-full rounded border px-3 py-1" value={draft.title ?? ""} onChange={set("title")} /></label>
            <label className="block text-sm">توضیحات<textarea className="mt-1 w-full rounded border px-3 py-1" rows={2} value={draft.description ?? ""} onChange={set("description")} /></label>
            <label className="block text-sm">دستورالعمل<textarea className="mt-1 w-full rounded border px-3 py-1" rows={6} value={draft.instructions ?? ""} onChange={set("instructions")} /></label>
            <label className="flex items-center gap-2 text-sm">
              نوع
              <select className="rounded border px-2 py-1" value={draft.type ?? "agent"} onChange={set("type")}>
                <option value="agent">agent (حالت‌مند، ابزار‌دار)</option>
                <option value="assistant">assistant (ساده)</option>
              </select>
            </label>
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
            <p className="text-xs text-slate-500">
              ابزارهای مجاز این تعریف. «جستجوی دانش سراسری» پایگاه‌های منتسب تب «دانش سراسری» را می‌خواند؛ اگر فقط «جستجوی دانش ورک‌اسپیس» تیک بخورد، دانش سراسری هم (سازگار با قبل) لحاظ می‌شود. «جستجوی اینترنتی» و «جستجوی مکان» در هر درخواست حداکثر یکی اجرا می‌شوند و پیش‌فرض خاموش‌اند.
            </p>
            {KNOWN_TOOLS.map((t) => {
              const list: string[] = Array.isArray(draft.tools?.tools)
                ? draft.tools.tools
                : Array.isArray(draft.tools) ? draft.tools : [];
              const on = list.includes(t.id);
              return (
                <label key={t.id} className="flex items-start gap-2 rounded border p-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={on}
                    onChange={() => {
                      const next = on ? list.filter((x) => x !== t.id) : [...list, t.id];
                      setDraft((d: any) => ({ ...d, tools: { tools: next } }));
                    }}
                  />
                  <span>
                    <strong>{t.label}</strong> <span className="font-mono text-xs text-slate-400" dir="ltr">{t.id}</span>
                    <span className="block text-xs text-slate-500">{t.desc}</span>
                  </span>
                </label>
              );
            })}
          </div>
        )}
        {tab === "workflow" && (<JsonArea value={draft.workflow} onChange={(v) => setDraft((d: any) => ({ ...d, workflow: v }))} />)}
        {tab === "model" && (
          <ModelTab draft={draft} setDraft={setDraft} eff={eff} />
        )}
        {tab === "prompts" && (
          <div className="space-y-4">
            <p className="text-xs text-slate-500">
              متن خالی یعنی «پیش‌فرض داخلی موتور».{" "}
              <span dir="ltr" className="font-mono">{"{placeholder}"}</span>‌های استفاده‌نشده باعث بازگشت به پیش‌فرض می‌شوند — قالب خراب هیچ‌وقت پاسخ را خراب نمی‌کند.
            </p>
            {(() => {
              const relevant = PROMPTS_FOR_KEY[def.key];
              const visible = relevant ? promptDocs.filter((t) => relevant.includes(t.key)) : promptDocs;
              return (
                <>
                  {relevant && (
                    <p className="rounded border bg-green-50 px-3 py-1.5 text-xs text-green-800">
                      این ایجنت (<span className="font-mono" dir="ltr">{def.key}</span>) فقط از {relevant.length} قالب استفاده می‌کند؛ بقیه نمایش داده نمی‌شوند.
                    </p>
                  )}
                  {!relevant && (
                    <p className="rounded border bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
                      ایجنت سفارشی: همه قالب‌ها نمایش داده می‌شوند. فقط قالب موردنیازت را پر کن و بقیه را خالی بگذار (پیش‌فرض داخلی).
                    </p>
                  )}
                  {visible.map((t) => {
              const cur: string = draft.prompt_templates?.[t.key] ?? "";
              const fa = PROMPT_FA[t.key];
              const overridden = cur !== "";
              return (
                <div key={t.key} className={`rounded border p-3 ${overridden ? "border-blue-300 bg-blue-50/40" : ""}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-bold">
                      {fa ? fa.title : t.key}{" "}
                      <span className="font-mono font-normal text-xs text-slate-400" dir="ltr">{t.key}</span>
                      {overridden && (
                        <span className="mr-2 rounded bg-blue-700 px-2 py-0.5 text-[11px] font-normal text-white">سفارشی‌شده</span>
                      )}
                    </h3>
                    <span className="text-xs text-slate-500">{fa ? fa.guide : t.description}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(t.placeholders ?? []).map((p: string) => (
                      <code key={p} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px]" dir="ltr">{"{" + p + "}"}</code>
                    ))}
                  </div>
                  <textarea
                    className="mt-2 w-full rounded border px-3 py-1 font-mono text-xs"
                    rows={8}
                    dir="ltr"
                    placeholder="(خالی = پیش‌فرض داخلی)"
                    value={cur}
                    onChange={(e) =>
                      setDraft((d: any) => ({
                        ...d,
                        prompt_templates: { ...(d.prompt_templates ?? {}), [t.key]: e.target.value },
                      }))
                    }
                  />
                  <div className="mt-1 flex gap-2 text-xs">
                    <button
                      className="rounded border px-2 py-0.5"
                      onClick={() => setShowDefault(showDefault === t.key ? null : t.key)}
                    >
                      {showDefault === t.key ? "پنهان‌کردن پیش‌فرض" : "نمایش پیش‌فرض داخلی"}
                    </button>
                    {cur !== "" && (
                      <button
                        className="rounded border px-2 py-0.5 text-red-600"
                        onClick={() =>
                          setDraft((d: any) => {
                            const next = { ...(d.prompt_templates ?? {}) };
                            delete next[t.key];
                            return { ...d, prompt_templates: next };
                          })
                        }
                      >
                        بازگشت به پیش‌فرض
                      </button>
                    )}
                  </div>
                  {showDefault === t.key && (
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-[11px]" dir="ltr">
                      {t.default}
                    </pre>
                  )}
                </div>
              );
            })}
                </>
              );
            })()}
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
              {allGlobal.length === 0 && (
                <p className="text-sm text-slate-400">
                  پایگاه سراسری وجود ندارد — از بخش{" "}
                  <a className="text-blue-700 hover:underline" href="/admin/global-knowledge">پایگاه‌های دانش سراسری</a>{" "}
                  بساز.
                </p>
              )}
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
