"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

const STATUS_STYLE: Record<string, string> = {
  ready: "text-green-700",
  failed: "text-red-700",
  pending: "text-amber-700",
  processing: "text-blue-700",
};

export default function ProcessingPage() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState("");

  const reload = () => {
    if (ws && kb)
      api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`)
        .then(setItems)
        .catch((e: Error) => setError(String(e)));
    else setItems([]);
  };
  useEffect(reload, [ws, kb]);

  const counts = items.reduce<Record<string, number>>((acc, s) => {
    acc[s.status] = (acc[s.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <main>
      <h1 className="text-xl font-bold">وضعیت پردازش</h1>
      <div className="mt-4 flex gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
        <KBSelect wsId={ws} value={kb} onChange={setKb} />
        <button className="rounded border px-3 py-1" onClick={reload}>
          به‌روزرسانی
        </button>
      </div>
      {kb && (
        <p className="mt-3 text-sm">
          {Object.entries(counts).map(([k, v]) => (
            <span key={k} className={`ml-4 ${STATUS_STYLE[k] ?? ""}`}>
              {k}: {v}
            </span>
          ))}
          {items.length === 0 && "سورسی نیست."}
        </p>
      )}
      <FormError e={error} />
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-right text-slate-500">
            <th className="p-2">فایل</th>
            <th className="p-2">نوع</th>
            <th className="p-2">وضعیت</th>
            <th className="p-2">خطا / توضیح</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-t">
              <td className="p-2">{s.filename}</td>
              <td className="p-2">{s.type}</td>
              <td className={`p-2 ${STATUS_STYLE[s.status] ?? ""}`}>{s.status}</td>
              <td className="p-2 text-xs text-slate-600">
                {s.error || (s.status === "pending" ? "در صف ورکر (Redis/Worker را بررسی کنید)" : "—")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
