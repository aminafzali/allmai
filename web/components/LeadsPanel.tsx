"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE, api, getToken } from "@/lib/admin";
import { KBSelect } from "@/components/Selectors";

type Lead = {
  id: string; query: string; name: string; address: string; phone: string;
  hours: string; website: string; lat: number | null; lng: number | null;
  source: string; status: string; created_at: string;
};

/** Workspace lead list: table + CSV/Excel download + status + save-to-KB. */
export default function LeadsPanel({ wsId }: { wsId: string }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [kb, setKb] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    if (!wsId) return;
    setLoading(true);
    setError("");
    api(`/workspaces/${wsId}/leads?limit=200`)
      .then(setLeads)
      .catch((e: Error) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [wsId]);

  useEffect(() => { load(); setChecked({}); }, [load]);

  async function download(fmt: "csv" | "xlsx") {
    setError("");
    const r = await fetch(`${API_BASE}/workspaces/${wsId}/leads/export?format=${fmt}`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    if (!r.ok) {
      setError(`download failed: ${r.status}`);
      return;
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fmt === "xlsx" ? "leads.xlsx" : "leads.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function setStatus(id: string, status: string) {
    setError("");
    try {
      await api(`/workspaces/${wsId}/leads/status`, {
        method: "PATCH", json: { lead_ids: [id], status },
      });
      load();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  async function importToKb() {
    const ids = Object.keys(checked).filter((k) => checked[k]);
    if (!ids.length || !kb) {
      setError("لید و پایگاه دانش را انتخاب کن");
      return;
    }
    setError("");
    setMsg("");
    try {
      const r = await api(`/workspaces/${wsId}/knowledge-bases/${kb}/leads/import`, {
        method: "POST", json: { lead_ids: ids },
      });
      setMsg(`وارد دانش شد (${ids.length} لید) — پردازش در پس‌زمینه`);
      setChecked({});
      void r;
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  const toggle = (id: string) => setChecked((c) => ({ ...c, [id]: !c[id] }));

  if (!wsId) return null;
  return (
    <section className="mt-6 rounded border bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-bold">لیدهای استخراج‌شده ({leads.length})</h2>
        <button className="rounded border px-2 py-1 text-xs" onClick={load} disabled={loading}>
          {loading ? "…" : "↻ به‌روزرسانی"}
        </button>
        <button className="rounded border px-2 py-1 text-xs" onClick={() => download("csv")}>
          دانلود CSV
        </button>
        <button className="rounded border px-2 py-1 text-xs" onClick={() => download("xlsx")}>
          دانلود Excel
        </button>
        <span className="flex items-center gap-1 text-xs">
          <KBSelect wsId={wsId} value={kb} onChange={setKb} />
          <button className="rounded border px-2 py-1" onClick={importToKb}>
            ذخیره منتخب در دانش
          </button>
        </span>
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      {msg && <p className="mt-1 text-xs text-green-700">{msg}</p>}
      {leads.length > 0 && (
        <div className="mt-2 max-h-72 overflow-auto">
          <table className="w-full text-xs" dir="rtl">
            <thead>
              <tr className="border-b text-slate-500">
                <th></th><th className="p-1 text-right">نام</th>
                <th className="p-1 text-right">آدرس</th><th className="p-1 text-right">تلفن</th>
                <th className="p-1">منبع</th><th className="p-1">وضعیت</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id} className="border-b last:border-0">
                  <td className="p-1"><input type="checkbox" checked={!!checked[l.id]} onChange={() => toggle(l.id)} /></td>
                  <td className="p-1 font-bold">{l.name}</td>
                  <td className="p-1 text-slate-600">{l.address}</td>
                  <td className="p-1 font-mono" dir="ltr">{l.phone}</td>
                  <td className="p-1 text-slate-500" dir="ltr">{l.source}</td>
                  <td className="p-1">
                    <select className="rounded border px-1" value={l.status}
                      onChange={(e) => setStatus(l.id, e.target.value)}>
                      <option value="new">جدید</option>
                      <option value="contacted">تماس گرفته</option>
                      <option value="archived">بایگانی</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
