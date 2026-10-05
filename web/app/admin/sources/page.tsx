"use client";

import { useEffect, useState } from "react";
import { API_BASE, api, getToken } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

const FILE_TYPES = ["pdf", "docx", "pptx", "txt", "md", "image", "audio", "video", "excel", "csv"];

export default function SourcesPage() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [stype, setStype] = useState("pdf");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [transcript, setTranscript] = useState<any | null>(null);
  const [tBusy, setTBusy] = useState(false);

  const reload = () => {
    if (ws && kb)
      api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`)
        .then(setItems)
        .catch((e: Error) => setError(String(e)));
    else setItems([]);
  };
  useEffect(reload, [ws, kb]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
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
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  async function addLink(kind: "note" | "url") {
    setError("");
    try {
      await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/link`, {
        method: "POST",
        json: kind === "note" ? { type: "note", title: "note", content: note } : { type: "url", title: url, url },
      });
      setNote("");
      setUrl("");
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  async function openTranscript(id: string) {
    setError("");
    setTBusy(true);
    try {
      setTranscript(await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/${id}/transcript`));
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setTBusy(false);
    }
  }

  function downloadTranscript() {
    if (!transcript?.text) return;
    const blob = new Blob([transcript.text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${transcript.filename || "transcript"}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  async function download(id: string, filename: string) {
    setError("");
    try {
      // Authenticated backend download (works for local + s3, never public).
      const { API_BASE, getToken, refreshAccess } = await import("@/lib/admin");
      let t = getToken();
      let r = await fetch(`${API_BASE}/workspaces/${ws}/sources/${id}/download`, {
        headers: t ? { Authorization: `Bearer ${t}` } : {},
      });
      if (r.status === 401) {
        t = await refreshAccess();
        r = await fetch(`${API_BASE}/workspaces/${ws}/sources/${id}/download`, {
          headers: t ? { Authorization: `Bearer ${t}` } : {},
        });
      }
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || "file";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">سورس‌ها</h1>
      <div className="mt-4 flex gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
        <KBSelect wsId={ws} value={kb} onChange={setKb} />
      </div>
      {ws && kb && (
        <>
          <form onSubmit={upload} className="mt-4 flex flex-wrap items-center gap-2">
            <select className="rounded border px-2 py-1" value={stype} onChange={(e) => setStype(e.target.value)}>
              {FILE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit" disabled={busy || !file}>
              {busy ? "…" : "آپلود"}
            </button>
          </form>
          <div className="mt-3 flex flex-wrap gap-2">
            <input
              className="rounded border px-3 py-1"
              placeholder="متن نوت"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="rounded border px-3 py-1" onClick={() => addLink("note")}>
              افزودن نوت
            </button>
            <input
              className="rounded border px-3 py-1"
              placeholder="https://…"
              dir="ltr"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <button className="rounded border px-3 py-1" onClick={() => addLink("url")}>
              افزودن URL
            </button>
          </div>
        </>
      )}
      <FormError e={error} />
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-right text-slate-500">
            <th className="p-2">فایل</th>
            <th className="p-2">نوع</th>
            <th className="p-2">وضعیت</th>
            <th className="p-2">خطا</th>
            <th className="p-2">عمل</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-t">
              <td className="p-2">{s.filename}</td>
              <td className="p-2">{s.type}</td>
              <td className="p-2">
                <span className={s.status === "ready" ? "text-green-700" : s.status === "failed" ? "text-red-700" : "text-amber-700"}>
                  {s.status}
                </span>
              </td>
              <td className="p-2 text-xs text-red-600">{s.error}</td>
              <td className="p-2">
                <button className="text-blue-700 hover:underline" onClick={() => download(s.id, s.filename)}>
                  دانلود
                </button>
                {s.status === "ready" && (
                  <button className="mr-3 text-green-700 hover:underline" onClick={() => openTranscript(s.id)}>
                    متن
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {tBusy && <p className="mt-2 text-sm text-slate-500">در حال بارگذاری متن…</p>}
      {transcript && (
        <section className="mt-4 rounded border bg-white p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-bold">متن استخراج‌شده: {transcript.filename}</h2>
            <div className="flex gap-2">
              <button className="rounded bg-green-700 px-3 py-1 text-sm text-white" onClick={downloadTranscript}>
                دانلود txt
              </button>
              <button className="rounded border px-3 py-1 text-sm" onClick={() => setTranscript(null)}>
                بستن
              </button>
            </div>
          </div>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm">
            {transcript.text || "متنی استخراج نشده است."}
          </pre>
        </section>
      )}
    </main>
  );
}
