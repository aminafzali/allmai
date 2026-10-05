"use client";

import { useEffect, useRef, useState } from "react";
import { Check, FilePlus, Sparkles, X } from "lucide-react";
import { api } from "@/lib/admin";

export default function AddToDocs({
  ws,
  kb,
  text,
  sourceId,
  defaultTitle = "",
  onSaved,
  onSave,
  compact = false,
  tone = "dark",
}: {
  ws: string;
  kb: string;
  text: string;
  sourceId?: string | null;
  defaultTitle?: string;
  onSaved?: (source: any) => void;
  /** Optional custom save target (e.g. one audio+transcript meeting source). */
  onSave?: (title: string) => Promise<any>;
  compact?: boolean;
  /** "light" = on colored bubbles (orange): bright trigger like Copy */
  tone?: "dark" | "light";
}) {
  const light = tone === "light";
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(defaultTitle);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  function openBox() {
    setOpen((v) => !v);
    setDone("");
    setError("");
    if (!title) {
      const first = (text || "").split("\n").map((s) => s.trim()).filter(Boolean)[0] ?? "";
      if (first) setTitle(first.slice(0, 60));
    }
  }

  async function aiTitle() {
    if (!ws || !kb || !text.trim()) return;
    setAiBusy(true);
    setError("");
    try {
      const r: any = await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/suggest-title`,
        { method: "POST", json: { text: text.slice(0, 2000) } }
      );
      if (r?.title) setTitle(String(r.title).slice(0, 80));
    } catch (e: any) {
      setError(String(e?.message ?? e).slice(0, 200));
    } finally {
      setAiBusy(false);
    }
  }

  async function save() {
    if (!ws || !kb) {
      setError("اول پایگاه دانش را انتخاب کن.");
      return;
    }
    const t = title.trim();
    if (!t) {
      setError("عنوان را بنویس یا با هوش مصنوعی بساز.");
      return;
    }
    if (!text.trim() && !sourceId) {
      setError("متنی برای ذخیره نیست.");
      return;
    }
    setBusy(true);
    setError("");
    setDone("");
    try {
      let src: any = null;
      if (onSave) {
        src = await onSave(t);
        setDone("در اسناد دانش ذخیره شد ✓");
      } else if (sourceId) {
        // audio/meeting doc already in KB -> just rename to chosen title
        src = await api(
          `/workspaces/${ws}/knowledge-bases/${kb}/sources/${sourceId}/rename`,
          { method: "PATCH", json: { title: t } }
        );
        setDone("عنوان سند صوتی به‌روز شد و در دانش است ✓");
      } else {
        src = await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/link`, {
          method: "POST",
          json: { type: "note", title: t, content: text },
        });
        setDone("وارد اسناد و دانش (RAG) شد ✓");
      }
      onSaved?.(src);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setOpen(false), 1400);
    } catch (e: any) {
      setError(String(e?.message ?? e).slice(0, 300));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="relative inline-block">
      <button
        onClick={openBox}
        title="اضافه کردن به اسناد"
        className={
          light
            ? `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition hover:bg-white/10 hover:text-white ${
                open ? "text-white" : "text-white/80"
              }`
            : `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition hover:bg-amber-50 hover:text-amber-700 ${
                open ? "text-amber-700" : "text-slate-400"
              }`
        }
      >
        <FilePlus className="h-3.5 w-3.5" />
        {!compact && <span>افزودن به اسناد</span>}
      </button>
      {open && (
        <span className="absolute bottom-7 right-0 z-30 block w-72 rounded-2xl border bg-white p-3 text-right text-slate-800 shadow-xl">
          <span className="flex items-center justify-between">
            <span className="text-xs font-extrabold text-slate-500">ذخیره در اسناد دانش</span>
            <button
              onClick={() => setOpen(false)}
              className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              aria-label="بستن"
            >
              <X className="h-4 w-4" />
            </button>
          </span>
          <input
            autoFocus
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="عنوان سند…"
            className="mt-2 w-full rounded-xl border px-3 py-2 text-xs text-slate-800 outline-none placeholder:text-slate-400 focus:border-amber-400"
          />
          <span className="mt-2 flex gap-1.5">
            <button
              onClick={aiTitle}
              disabled={aiBusy || !text.trim()}
              className="flex flex-1 items-center justify-center gap-1 rounded-xl border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] font-bold text-amber-800 disabled:opacity-50 hover:bg-amber-100"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {aiBusy ? "…" : "ساخت عنوان با AI"}
            </button>
            <button
              onClick={save}
              disabled={busy || !title.trim()}
              className="flex flex-1 items-center justify-center gap-1 rounded-xl bg-amber-600 px-2 py-1.5 text-[11px] font-bold text-white disabled:opacity-50 hover:bg-amber-700"
            >
              <Check className="h-3.5 w-3.5" />
              {busy ? "…" : sourceId ? "ثبت عنوان" : "ذخیره در اسناد"}
            </button>
          </span>
          {done && <span className="mt-1.5 block text-[11px] text-green-700">{done}</span>}
          {error && <span className="mt-1.5 block text-[11px] text-red-600">{error}</span>}
          <span className="mt-1.5 block text-[10px] leading-4 text-slate-400">
            {sourceId
              ? "این سند صوتی از قبل در پایگاه دانش است؛ فقط عنوانش ثبت می‌شود."
              : "متن پیام به‌عنوان یادداشت در همین پایگاه دانش ذخیره و وارد RAG می‌شود."}
          </span>
        </span>
      )}
    </span>
  );
}
