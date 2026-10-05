"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Download, FileUp, Link2, Pencil, Plus, RefreshCw, StickyNote, Trash2, X } from "lucide-react";
import { API_BASE, api, getToken } from "@/lib/admin";
import { NOTES_WS_KEY } from "@/lib/notes";
import KbPicker from "@/components/notes/KbPicker";
import { FormError } from "@/components/Selectors";
import { StatusDot, fileVisual, formatBytes } from "@/components/notes/FileIcon";

type Source = {
  id: string; type: string; filename: string; status: string;
  size_bytes?: number; created_at?: string; error?: string;
  processing_started_at?: string | null;
};

function detectType(name: string): string {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (["doc", "docx"].includes(ext)) return "docx";
  if (["ppt", "pptx"].includes(ext)) return "pptx";
  if (ext === "txt") return "txt";
  if (ext === "md") return "md";
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) return "image";
  if (["mp3", "wav", "m4a", "ogg", "flac"].includes(ext)) return "audio";
  if (["mp4", "mov", "webm", "mkv"].includes(ext)) return "video";
  if (["xls", "xlsx"].includes(ext)) return "excel";
  if (ext === "csv") return "csv";
  return "pdf";
}

export default function NotesFiles() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");  const [items, setItems] = useState<Source[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [kbTitle, setKbTitle] = useState("");

  // sheets: null | "menu" | "file" | "note" | "link" | "preview"
  const [sheet, setSheet] = useState<null | string>(null);
  const [file, setFile] = useState<File | null>(null);
  const [stype, setStype] = useState("pdf");
  const [noteTitle, setNoteTitle] = useState("");
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<any | null>(null);
  const [previewSrc, setPreviewSrc] = useState<Source | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [revising, setRevising] = useState(false);
  const [revised, setRevised] = useState("");
  const [retrying, setRetrying] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(NOTES_WS_KEY);
      if (saved) setWs(saved);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const reload = useCallback(() => {
    if (ws && kb)
      api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`)
        .then(setItems)
        .catch((e: Error) => setError(String(e?.message ?? e)));
    else setItems([]);
  }, [ws, kb]);
  useEffect(reload, [reload]);

  // auto-refresh while anything is still processing
  useEffect(() => {
    if (!items.some((s) => s.status === "processing" || s.status === "pending")) return;
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [items, reload]);

  async function createKb(e: React.FormEvent) {
    e.preventDefault();
    if (!ws || !kbTitle.trim()) return;
    setError("");
    try {
      const created: any = await api(`/workspaces/${ws}/knowledge-bases`, {
        method: "POST",
        json: { title: kbTitle.trim() },
      });
      setKbTitle("");
      if (created?.id) setKb(created.id);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !ws || !kb) return;
    setError("");
    setBusy(true);
    try {
      const form = new FormData();
      form.append("type", stype);
      form.append("file", file);
      const t = getToken();
      const r = await fetch(
        `${API_BASE}/workspaces/${ws}/knowledge-bases/${kb}/sources`,
        { method: "POST", headers: t ? { Authorization: `Bearer ${t}` } : {}, body: form }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
      setFile(null);
      setSheet(null);
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  async function addLink(kind: "note" | "url") {
    if (!ws || !kb) return;
    setError("");
    setBusy(true);
    try {
      await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/link`, {
        method: "POST",
        json: kind === "note"
          ? { type: "note", title: noteTitle.trim() || "یادداشت", content: note }
          : { type: "url", title: url, url },
      });
      setNote("");
      setNoteTitle("");
      setUrl("");
      setSheet(null);
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  async function removeSource(s: Source) {
    if (!ws || !kb) return;
    if (!window.confirm(`«${s.filename}» حذف شود؟ متن استخراج‌شده و چانک‌ها هم پاک می‌شوند.`)) return;
    setError("");
    try {
      await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/${s.id}`, { method: "DELETE" });
      if (previewSrc?.id === s.id) {
        setSheet(null);
        setPreviewSrc(null);
      }
      reload();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  async function openPreview(s: Source) {
    setPreviewSrc(s);
    setPreview(null);
    setEditing(false);
    setRevised("");
    setInstruction("");
    setSheet("preview");
    setPreviewLoading(true);
    try {
      const t = await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/${s.id}/transcript`);
      setPreview(t);
    } catch (e: any) {
      setPreview({ error: String(e?.message ?? e) });
    } finally {
      setPreviewLoading(false);
    }
  }

  async function downloadOriginal(s: Source) {
    const t = getToken();
    const r = await fetch(
      `${API_BASE}/workspaces/${ws}/sources/${s.id}/download`,
      { headers: t ? { Authorization: `Bearer ${t}` } : {} }
    );
    if (!r.ok) {
      setError(`download failed: ${r.status}`);
      return;
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = s.filename || "file";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function downloadText() {
    if (!preview?.text) return;
    const blob = new Blob([preview.text], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${previewSrc?.filename ?? "text"}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function saveEditText(text: string) {
    if (!previewSrc || !text.trim()) return;
    setError("");
    setSaving(true);
    try {
      await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/sources/${previewSrc.id}/text`,
        { method: "PUT", json: { text } }
      );
      setEditing(false);
      setRevised("");
      setPreview(await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/sources/${previewSrc.id}/transcript`
      ));
      reload();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  async function reviseWithAI() {
    if (!previewSrc || !instruction.trim()) return;
    setError("");
    setRevising(true);
    try {
      const r: any = await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/sources/${previewSrc.id}/revise`,
        { method: "POST", json: { instruction: instruction.trim() } }
      );
      setRevised(r.revised ?? "");
      setEditing(false);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setRevising(false);
    }
  }

  async function retryIngest() {
    if (!previewSrc) return;
    setError("");
    setRetrying(true);
    try {
      await api(`/workspaces/${ws}/sources/${previewSrc.id}/retry`, { method: "POST" });
      setSheet(null);
      reload();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setRetrying(false);
    }
  }

  async function copyText() {
    if (!preview?.text) return;
    try {
      await navigator.clipboard.writeText(preview.text);
    } catch {
      /* clipboard unavailable */
    }
  }

  const filtered = items.filter((s) =>
    !query.trim() || (s.filename ?? "").includes(query.trim())
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <KbPicker ws={ws} value={kb} onChange={setKb} />
        <button
          onClick={reload}
          title="به‌روزرسانی"
          className="rounded-full border bg-white p-2 text-slate-500 hover:bg-slate-100"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
        <a href="/notes" className="ms-auto text-xs text-slate-400 hover:text-slate-600">
          تغییر ورک‌اسپیس
        </a>
      </div>

      {ws && !kb && (
        <form onSubmit={createKb} className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded-xl border px-3 py-2"
            placeholder="نام بیس دانش جدید…"
            value={kbTitle}
            onChange={(e) => setKbTitle(e.target.value)}
          />
          <button className="rounded-xl bg-amber-600 px-4 py-2 text-sm text-white" type="submit">
            ساخت بیس
          </button>
        </form>
      )}
      <FormError e={error} />

      {kb && (
        <>
          <div className="mt-4">
            <input
              className="w-full rounded-full border bg-white px-4 py-2 text-sm"
              placeholder="جستجو در فایل‌ها…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {filtered.length === 0 ? (
            <div className="mt-10 text-center text-sm text-slate-400">
              <p className="text-4xl">📁</p>
              <p className="mt-2">هنوز فایلی نیست — با دکمه ＋ اضافه کن.</p>
            </div>
          ) : (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {filtered.map((s) => {
                const v = fileVisual(s.type);
                const Icon = v.icon;
                return (
                  <div
                    key={s.id}
                    onClick={() => openPreview(s)}
                    className="relative rounded-2xl border bg-white p-3 text-right shadow-sm transition hover:shadow active:scale-[0.99]"
                  >
                    <button
                      onClick={(e) => { e.stopPropagation(); removeSource(s); }}
                      title="حذف فایل و داده‌هایش"
                      className="absolute left-2 top-2 rounded-full p-1.5 text-slate-300 hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                    <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${v.bg}`}>
                      <Icon className={`h-6 w-6 ${v.fg}`} />
                    </span>
                    <p className="mt-2 truncate text-xs font-bold" title={s.filename}>
                      {s.filename}
                    </p>
                    <p className="mt-0.5 text-[11px] text-slate-400">
                      {s.type} {s.size_bytes ? `· ${formatBytes(s.size_bytes)}` : ""}
                    </p>
                    <p className="mt-1">
                      <StatusDot status={s.status} since={s.processing_started_at ?? undefined} />
                    </p>
                    {s.status === "failed" && (
                      <p className="mt-1 text-[11px] text-red-600">
                        {(s.error || "ناموفق شد — «استخراج مجدد» را بزن.").slice(0, 120)}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* FAB */}
      {kb && (
        <button
          onClick={() => setSheet("menu")}
          aria-label="افزودن"
          className="fixed bottom-20 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-amber-600 text-white shadow-lg transition hover:bg-amber-700 active:scale-95 md:bottom-8"
          style={{ left: "max(1rem, calc(50% - 24rem))" }}
        >
          <Plus className="h-7 w-7" />
        </button>
      )}

      {/* bottom sheet */}
      {sheet && (
        <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/40" onClick={() => !busy && setSheet(null)} />
          <div className="absolute inset-x-0 bottom-0 mx-auto w-full max-w-3xl rounded-t-3xl bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-xl md:bottom-8 md:rounded-3xl">
            <div className="mx-auto mb-3 h-1 w-10 rounded bg-slate-200 md:hidden" />
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-extrabold">
                {sheet === "menu" && "چه چیزی اضافه شود؟"}
                {sheet === "file" && "آپلود فایل"}
                {sheet === "note" && "یادداشت جدید"}
                {sheet === "link" && "افزودن لینک"}
                {sheet === "preview" && (previewSrc?.filename ?? "پیش‌نمایش")}
              </h2>
              <button onClick={() => setSheet(null)} className="rounded-full p-1 hover:bg-slate-100" aria-label="بستن">
                <X className="h-5 w-5" />
              </button>
            </div>

            {sheet === "menu" && (
              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={() => { setSheet("file"); setTimeout(() => fileRef.current?.click(), 50); }}
                  className="flex flex-col items-center gap-1 rounded-2xl border p-4 hover:bg-slate-50"
                >
                  <FileUp className="h-6 w-6 text-amber-600" />
                  <span className="text-xs">فایل</span>
                </button>
                <button
                  onClick={() => setSheet("note")}
                  className="flex flex-col items-center gap-1 rounded-2xl border p-4 hover:bg-slate-50"
                >
                  <StickyNote className="h-6 w-6 text-amber-600" />
                  <span className="text-xs">یادداشت</span>
                </button>
                <button
                  onClick={() => setSheet("link")}
                  className="flex flex-col items-center gap-1 rounded-2xl border p-4 hover:bg-slate-50"
                >
                  <Link2 className="h-6 w-6 text-amber-600" />
                  <span className="text-xs">لینک</span>
                </button>
              </div>
            )}

            {sheet === "file" && (
              <form onSubmit={upload} className="space-y-3">
                <input
                  ref={fileRef}
                  type="file"
                  className="w-full text-sm"
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    setFile(f);
                    if (f) setStype(detectType(f.name));
                  }}
                />
                {file && (
                  <p className="text-xs text-slate-500">
                    {file.name} · {formatBytes(file.size)} · نوع: <strong>{stype}</strong>
                  </p>
                )}
                <button
                  className="w-full rounded-xl bg-amber-600 py-2 text-sm text-white disabled:opacity-50"
                  type="submit"
                  disabled={!file || busy}
                >
                  {busy ? "در حال ارسال…" : "ارسال فایل"}
                </button>
              </form>
            )}

            {sheet === "note" && (
              <div className="space-y-3">
                <input
                  className="w-full rounded-xl border px-3 py-2 text-sm"
                  placeholder="عنوان یادداشت…"
                  value={noteTitle}
                  onChange={(e) => setNoteTitle(e.target.value)}
                />
                <textarea
                  className="w-full rounded-xl border px-3 py-2 text-sm"
                  rows={5}
                  placeholder="متن یادداشت…"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
                <button
                  className="w-full rounded-xl bg-amber-600 py-2 text-sm text-white disabled:opacity-50"
                  onClick={() => addLink("note")}
                  disabled={!note.trim() || busy}
                >
                  ذخیره یادداشت
                </button>
              </div>
            )}

            {sheet === "link" && (
              <div className="space-y-3">
                <input
                  className="w-full rounded-xl border px-3 py-2 text-sm"
                  dir="ltr"
                  placeholder="https://… (یوتیوب / اینستاگرام / آپارات / صفحه وب)"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
                <button
                  className="w-full rounded-xl bg-amber-600 py-2 text-sm text-white disabled:opacity-50"
                  onClick={() => addLink("url")}
                  disabled={!url.trim() || busy}
                >
                  ارسال لینک
                </button>
              </div>
            )}

            {sheet === "preview" && previewSrc && (
              <div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <StatusDot status={previewSrc.status} since={previewSrc.processing_started_at ?? undefined} />
                  <span>{previewSrc.type}</span>
                  {previewSrc.size_bytes ? <span>· {formatBytes(previewSrc.size_bytes)}</span> : null}
                </div>
                {previewSrc.status === "failed" && (
                  <div className="mt-2 rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-6">
                    <p className="font-bold text-red-700">پردازش ناموفق بود</p>
                    <p className="mt-1 text-red-600">{(previewSrc.error || "دلیل نامشخص").slice(0, 300)}</p>
                    <button
                      onClick={() => retryIngest()}
                      disabled={retrying}
                      className="mt-2 w-full rounded-xl bg-red-600 py-2 text-xs font-bold text-white disabled:opacity-50"
                    >
                      {retrying ? "…" : "↻ استخراج مجدد"}
                    </button>
                  </div>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    onClick={() => downloadOriginal(previewSrc)}
                    className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-slate-50"
                  >
                    <Download className="h-3.5 w-3.5" /> دانلود اصلی
                  </button>
                  <button
                    onClick={() => retryIngest()}
                    disabled={retrying}
                    className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${retrying ? "animate-spin" : ""}`} />
                    {retrying ? "…" : "استخراج مجدد"}
                  </button>
                  <button
                    onClick={() => removeSource(previewSrc)}
                    className="flex items-center gap-1 rounded-full border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" /> حذف فایل
                  </button>
                  {preview?.text && (
                    <>
                      <button
                        onClick={downloadText}
                        className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-slate-50"
                      >
                        <Download className="h-3.5 w-3.5" /> دانلود متن
                      </button>
                      <button
                        onClick={copyText}
                        className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-slate-50"
                      >
                        <Copy className="h-3.5 w-3.5" /> کپی متن
                      </button>
                    </>
                  )}
                </div>
                {preview?.text && !editing && !revised && (
                  <button
                    onClick={() => { setEditText(preview.text); setEditing(true); }}
                    className="flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-800 hover:bg-amber-100"
                  >
                    <Pencil className="h-3.5 w-3.5" /> ویرایش متن
                  </button>
                )}
                <div className="mt-3 max-h-[45dvh] overflow-auto rounded-xl bg-slate-50 p-3 text-xs leading-6 md:max-h-96">
                  {previewLoading && <p className="text-slate-400">در حال خواندن متن…</p>}
                  {!previewLoading && preview?.error && <p className="text-red-600">{preview.error}</p>}
                  {!previewLoading && preview && !preview.error && !preview.text && (
                    <p className="text-slate-400">
                      {previewSrc.status === "ready"
                        ? "متنی استخراج نشده است."
                        : "سند هنوز در حال پردازش است — کمی بعد دوباره باز کن."}
                    </p>
                  )}
                  {!previewLoading && preview?.text && !editing && !revised && (
                    <p className="whitespace-pre-wrap">{preview.text}</p>
                  )}
                  {editing && (
                    <div>
                      <textarea
                        className="min-h-48 w-full rounded-xl border bg-white px-3 py-2 text-xs leading-6"
                        rows={12}
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                      />
                      <div className="mt-2 flex gap-2">
                        <button
                          onClick={() => saveEditText(editText)}
                          disabled={saving || !editText.trim()}
                          className="rounded-xl bg-amber-600 px-4 py-1.5 text-xs text-white disabled:opacity-50"
                        >
                          {saving ? "در حال ذخیره و ایندکس…" : "ذخیره متن جدید"}
                        </button>
                        <button
                          onClick={() => setEditing(false)}
                          className="rounded-xl border px-4 py-1.5 text-xs"
                        >
                          انصراف
                        </button>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-400">
                        متن جدید جایگزین ایندکس قبلی می‌شود و دوباره وارد RAG می‌گردد.
                      </p>
                    </div>
                  )}
                  {revised && (
                    <div>
                      <p className="mb-1 text-[11px] font-bold text-amber-700">پیش‌نمایش اصلاح هوش مصنوعی:</p>
                      <p className="whitespace-pre-wrap rounded-xl border border-amber-200 bg-white p-2">{revised}</p>
                      <div className="mt-2 flex gap-2">
                        <button
                          onClick={() => saveEditText(revised)}
                          disabled={saving}
                          className="rounded-xl bg-amber-600 px-4 py-1.5 text-xs text-white disabled:opacity-50"
                        >
                          {saving ? "…" : "ذخیره این متن"}
                        </button>
                        <button
                          onClick={() => setRevised("")}
                          className="rounded-xl border px-4 py-1.5 text-xs"
                        >
                          دور انداختن
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                {preview?.text && !editing && (
                  <div className="mt-2 flex gap-2">
                    <input
                      className="flex-1 rounded-xl border px-3 py-1.5 text-xs"
                      placeholder="به AI بگو متن را چطور استخراج/اصلاح کند… (مثلاً: فقط نکات کلیدی به فارسی)"
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                    />
                    <button
                      onClick={reviseWithAI}
                      disabled={revising || !instruction.trim()}
                      className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 disabled:opacity-50"
                    >
                      {revising ? "…" : "اصلاح با AI"}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
