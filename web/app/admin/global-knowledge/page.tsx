"use client";

import { useEffect, useState } from "react";
import { API_BASE, api, getToken } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

const FILE_TYPES = ["pdf", "docx", "pptx", "txt", "md", "image", "audio", "video", "excel", "csv"];

type GlobalKB = { id: string; title: string; description?: string };

export default function GlobalKnowledgePage() {
  const [kbs, setKbs] = useState<GlobalKB[]>([]);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [openKb, setOpenKb] = useState<string | null>(null);
  const [error, setError] = useState("");

  const reload = () => {
    api("/admin/global-knowledge-bases")
      .then(setKbs)
      .catch((e: Error) => setError(String(e)));
  };
  useEffect(reload, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/admin/global-knowledge-bases", {
        method: "POST",
        json: { title, description: desc },
      });
      setTitle("");
      setDesc("");
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">پایگاه‌های دانش سراسری</h1>
      <p className="mt-1 text-sm text-slate-500">
        فقط ادمین. مستقل از ورک‌اسپیس‌ها — در استودیوی ایجنت (تب «دانش سراسری») به ایجنت‌ها منتسب می‌شوند.
      </p>

      <form onSubmit={create} className="mt-4 flex flex-wrap gap-2 rounded border bg-white p-3">
        <input
          className="rounded border px-3 py-1"
          placeholder="عنوان پایگاه سراسری"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          className="rounded border px-3 py-1"
          placeholder="توضیح (اختیاری)"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
        />
        <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit">
          بساز
        </button>
      </form>
      <FormError e={error} />

      <ul className="mt-4 space-y-2">
        {kbs.map((k) => (
          <li key={k.id} className="rounded border bg-white">
            <button
              className="flex w-full items-center justify-between p-3 text-right"
              onClick={() => setOpenKb(openKb === k.id ? null : k.id)}
            >
              <span>
                <strong>{k.title}</strong>
                {k.description && <span className="mr-2 text-sm text-slate-500">{k.description}</span>}
              </span>
              <span className="text-xs text-slate-400">{openKb === k.id ? "▲" : "▼"}</span>
            </button>
            {openKb === k.id && <KbDetail kb={k} onError={setError} />}
          </li>
        ))}
      </ul>
      {kbs.length === 0 && <p className="mt-3 text-sm text-slate-400">پایگاه سراسری وجود ندارد.</p>}
    </main>
  );
}

function KbDetail({ kb, onError }: { kb: GlobalKB; onError: (e: string) => void }) {
  const [sources, setSources] = useState<any[]>([]);
  const [defs, setDefs] = useState<any[]>([]);
  const [assigned, setAssigned] = useState<Record<string, string[]>>({});
  const [stype, setStype] = useState("pdf");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [transcript, setTranscript] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);

  const base = `/admin/global-knowledge-bases/${kb.id}`;

  const reloadSources = () => {
    api(`${base}/sources`).then(setSources).catch((e: Error) => onError(String(e)));
  };
  const reloadDefs = () => {
    api("/admin/agent-definitions")
      .then((ds: any[]) => {
        setDefs(ds);
        ds.forEach((d) => {
          api(`/admin/agent-definitions/${d.id}/knowledge`)
            .then((r) => setAssigned((a) => ({ ...a, [d.id]: r.kb_ids ?? [] })))
            .catch(() => {});
        });
      })
      .catch((e: Error) => onError(String(e)));
  };
  useEffect(() => {
    reloadSources();
    reloadDefs();
  }, [kb.id]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    onError("");
    setBusy(true);
    try {
      const form = new FormData();
      form.append("type", stype);
      form.append("file", file);
      const t = getToken();
      const r = await fetch(`${API_BASE}${base}/sources`, {
        method: "POST",
        headers: t ? { Authorization: `Bearer ${t}` } : {},
        body: form,
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
      setFile(null);
      reloadSources();
    } catch (err: any) {
      onError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  async function addLink(kind: "note" | "url") {
    onError("");
    try {
      await api(`${base}/sources/link`, {
        method: "POST",
        json: kind === "note" ? { type: "note", title: "یادداشت سراسری", content: note } : { type: "url", title: url, url },
      });
      setNote("");
      setUrl("");
      reloadSources();
    } catch (err: any) {
      onError(String(err?.message ?? err));
    }
  }

  async function toggleAssign(defId: string) {
    onError("");
    const cur = assigned[defId] ?? [];
    const next = cur.includes(kb.id) ? cur.filter((k) => k !== kb.id) : [...cur, kb.id];
    try {
      const r = await api(`/admin/agent-definitions/${defId}/knowledge`, {
        method: "PUT",
        json: { kb_ids: next },
      });
      setAssigned((a) => ({ ...a, [defId]: r.kb_ids ?? next }));
    } catch (err: any) {
      onError(String(err?.message ?? err));
    }
  }

  async function openTranscript(id: string) {
    onError("");
    try {
      setTranscript(await api(`${base}/sources/${id}/transcript`));
    } catch (err: any) {
      onError(String(err?.message ?? err));
    }
  }

  async function removeSource(s: any) {
    if (!window.confirm(`«${s.filename}» حذف شود؟ متن استخراج‌شده و چانک‌ها هم پاک می‌شوند.`)) return;
    onError("");
    try {
      await api(`${base}/sources/${s.id}`, { method: "DELETE" });
      if (transcript?.source_id === s.id) setTranscript(null);
      reloadSources();
    } catch (err: any) {
      onError(String(err?.message ?? err));
    }
  }

  return (
    <div className="space-y-3 border-t p-3">
      <form onSubmit={upload} className="flex flex-wrap items-center gap-2">
        <select className="rounded border px-2 py-1" value={stype} onChange={(e) => setStype(e.target.value)}>
          {FILE_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="rounded bg-blue-700 px-3 py-1 text-white disabled:opacity-50" type="submit" disabled={!file || busy}>
          ارسال فایل
        </button>
      </form>
      <div className="flex flex-wrap gap-2">
        <input
          className="min-w-52 flex-1 rounded border px-3 py-1"
          placeholder="یادداشت سراسری…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button className="rounded border px-3 py-1" onClick={() => addLink("note")} disabled={!note.trim()}>
          ذخیره یادداشت
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        <input
          className="min-w-52 flex-1 rounded border px-3 py-1"
          dir="ltr"
          placeholder="https://… (یوتیوب / آپارات / صفحه وب)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="rounded border px-3 py-1" onClick={() => addLink("url")} disabled={!url.trim()}>
          ارسال لینک
        </button>
      </div>

      <ul className="space-y-1 text-sm">
        {sources.map((s: any) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded border px-3 py-1">
            <span className="min-w-0">
              {s.filename} <span className="text-slate-400">({s.type})</span>
              {s.status === "failed" && s.error && (
                <span className="block truncate text-xs text-red-600" title={s.error}>
                  {String(s.error).slice(0, 140)}
                </span>
              )}
            </span>
            <span className="flex items-center gap-2">
              <span className={s.status === "ready" ? "text-green-700" : s.status === "failed" ? "text-red-700" : "text-slate-500"}>
                {s.status}
              </span>
              <button className="text-blue-700 hover:underline" onClick={() => openTranscript(s.id)}>
                متن
              </button>
              <button className="text-red-600 hover:underline" onClick={() => removeSource(s)}>
                حذف
              </button>
            </span>
          </li>
        ))}
      </ul>
      {transcript?.text && (
        <div className="rounded border bg-slate-50 p-2">
          <div className="mb-1 flex justify-between text-xs text-slate-500">
            <span>{transcript.filename}</span>
            <button onClick={() => setTranscript(null)}>بستن</button>
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">{transcript.text.slice(0, 4000)}</pre>
        </div>
      )}

      <div className="rounded border bg-slate-50 p-2">
        <h3 className="text-sm font-bold">انتساب به ایجنت‌ها (مثل تب «دانش سراسری» استودیو)</h3>
        <ul className="mt-2 space-y-1">
          {defs.map((d: any) => {
            const on = (assigned[d.id] ?? []).includes(kb.id);
            return (
              <li key={d.id} className="flex items-center justify-between text-sm">
                <span>{d.title || d.key} <span className="font-mono text-xs text-slate-400" dir="ltr">{d.key}</span></span>
                <button
                  onClick={() => toggleAssign(d.id)}
                  className={`rounded px-3 py-0.5 text-white ${on ? "bg-red-600" : "bg-green-700"}`}
                >
                  {on ? "حذف انتساب" : "انتساب"}
                </button>
              </li>
            );
          })}
          {defs.length === 0 && <p className="text-xs text-slate-400">تعریفی وجود ندارد.</p>}
        </ul>
      </div>
    </div>
  );
}
