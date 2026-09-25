"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

const STATE_STYLE: Record<string, string> = {
  done: "bg-green-100 text-green-800",
  waiting: "bg-slate-100 text-slate-500",
  skipped: "bg-slate-100 text-slate-400",
  degraded: "bg-amber-100 text-amber-800",
  failed: "bg-red-100 text-red-700",
};

function Stages({ stages }: { stages: any[] }) {
  return (
    <ol className="mt-2 flex flex-wrap items-center gap-2">
      {stages.map((s, i) => (
        <li key={s.key} className="flex items-center gap-2">
          {i > 0 && <span className="text-slate-300">←</span>}
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATE_STYLE[s.state] ?? "bg-slate-100"}`} title={s.detail ?? ""}>
            {s.label}: {s.state}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Detail({ detail }: { detail: any }) {
  if (!detail) return <p className="mt-4 text-sm text-slate-400">سندی انتخاب نشده است.</p>;
  return (
    <section className="mt-4 space-y-4 rounded border bg-white p-4">
      <div>
        <h2 className="font-bold">{detail.filename} <span className="text-xs font-normal text-slate-500">({detail.type} · {detail.status})</span></h2>
        <Stages stages={detail.stages ?? []} />
        {detail.error && <p className="mt-2 text-xs text-red-600" dir="ltr">{detail.error}</p>}
      </div>
      <div>
        <h3 className="text-sm font-bold">figureها ({detail.figures?.length ?? 0}) · چانک‌ها: {detail.chunks ?? 0}</h3>
        {(detail.figures ?? []).length === 0 && <p className="text-xs text-slate-400">figureای نیست.</p>}
        <ul className="mt-1 space-y-2">
          {(detail.figures ?? []).map((f: any) => (
            <li key={f.chunk_id} className="rounded border p-2 text-xs">
              <p><strong>صفحه {f.page_no}</strong> · caption: {f.caption || "—"}</p>
              <p className="mt-1">
                <span className={`rounded px-2 py-0.5 ${STATE_STYLE[f.vision_status?.startsWith("skipped") ? "skipped" : f.vision_status === "described" ? "done" : f.vision_status === "failed" ? "failed" : "waiting"] ?? ""}`}>
                  vision: {f.vision_status}
                </span>
                {f.vision_uncertain && <span className="mr-2 rounded bg-amber-100 px-2 py-0.5 text-amber-800">uncertain</span>}
              </p>
              <p className="mt-1 text-slate-600">{f.excerpt}</p>
              {f.storage_key && <p className="mt-1 text-slate-400" dir="ltr">{f.storage_key}</p>}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="text-sm font-bold">OCR / Vision / metadata</h3>
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-[11px]" dir="ltr">
          {JSON.stringify(detail.parse_meta ?? {}, null, 2)}
        </pre>
      </div>
    </section>
  );
}

export default function DocumentsPage() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [sources, setSources] = useState<any[]>([]);
  const [sid, setSid] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [globals_, setGlobals] = useState<any[]>([]);
  const [gkb, setGkb] = useState("");
  const [gsources, setGsources] = useState<any[]>([]);
  const [gsid, setGsid] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api("/admin/global-knowledge-bases").then(setGlobals).catch(() => {});
  }, []);
  useEffect(() => {
    if (ws && kb)
      api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`).then((r) => { setSources(r); setSid(""); setDetail(null); }).catch((e: Error) => setError(String(e)));
    else { setSources([]); setSid(""); setDetail(null); }
  }, [ws, kb]);
  useEffect(() => {
    if (gkb)
      api(`/admin/global-knowledge-bases/${gkb}/sources`).then((r) => { setGsources(r); setGsid(""); }).catch((e: Error) => setError(String(e)));
    else { setGsources([]); setGsid(""); }
  }, [gkb]);

  async function openWorkspace() {
    if (!sid) return;
    setError("");
    try {
      setDetail(await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources/${sid}/processing`));
    } catch (e: any) { setError(String(e?.message ?? e)); }
  }
  async function openGlobal() {
    if (!gsid) return;
    setError("");
    try {
      setDetail(await api(`/admin/global-knowledge-bases/${gkb}/sources/${gsid}/processing`));
    } catch (e: any) { setError(String(e?.message ?? e)); }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">اسناد (Document Core)</h1>
      <p className="mt-1 text-sm text-slate-500">وضعیت خط پردازش هر سند: آپلود، تجزیه، OCR، Vision، چانک، نتیجه + figureها و metadata.</p>

      <div className="mt-4 rounded border bg-white p-4">
        <h2 className="font-bold">ورک‌اسپیس</h2>
        <div className="mt-2 flex flex-wrap gap-2">
          <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
          <KBSelect wsId={ws} value={kb} onChange={setKb} />
          <select className="rounded border px-2 py-1" value={sid} onChange={(e) => setSid(e.target.value)}>
            <option value="">— سند —</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>{s.filename} ({s.status})</option>
            ))}
          </select>
          <button className="rounded bg-blue-700 px-4 py-1 text-white" onClick={openWorkspace} disabled={!sid}>
            نمایش
          </button>
        </div>
      </div>

      <div className="mt-4 rounded border bg-white p-4">
        <h2 className="font-bold">دانش سراسری</h2>
        <div className="mt-2 flex flex-wrap gap-2">
          <select className="rounded border px-2 py-1" value={gkb} onChange={(e) => setGkb(e.target.value)}>
            <option value="">— پایگاه سراسری —</option>
            {globals_.map((k) => (
              <option key={k.id} value={k.id}>{k.title}</option>
            ))}
          </select>
          <select className="rounded border px-2 py-1" value={gsid} onChange={(e) => setGsid(e.target.value)}>
            <option value="">— سند —</option>
            {gsources.map((s) => (
              <option key={s.id} value={s.id}>{s.filename} ({s.status})</option>
            ))}
          </select>
          <button className="rounded bg-blue-700 px-4 py-1 text-white" onClick={openGlobal} disabled={!gsid}>
            نمایش
          </button>
        </div>
      </div>

      <FormError e={error} />
      <Detail detail={detail} />
    </main>
  );
}
