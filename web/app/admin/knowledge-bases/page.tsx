"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function KBsPage() {
  const [ws, setWs] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [error, setError] = useState("");

  const reload = () => {
    if (ws) api(`/workspaces/${ws}/knowledge-bases`).then(setItems).catch((e: Error) => setError(String(e)));
    else setItems([]);
  };
  useEffect(reload, [ws]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(`/workspaces/${ws}/knowledge-bases`, {
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
      <h1 className="text-xl font-bold">بیس‌های دانش</h1>
      <div className="mt-4">
        <WorkspaceSelect value={ws} onChange={setWs} />
      </div>
      {ws && (
        <form onSubmit={create} className="mt-4 flex gap-2">
          <input
            className="rounded border px-3 py-1"
            placeholder="عنوان بیس دانش"
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
      )}
      <FormError e={error} />
      <ul className="mt-4 space-y-2">
        {items.map((k) => (
          <li key={k.id} className="rounded border bg-white p-3">
            <strong>{k.title}</strong>
            {k.description && <p className="text-sm text-slate-600">{k.description}</p>}
            <div className="mt-1 text-xs text-slate-500" dir="ltr">{k.id}</div>
          </li>
        ))}
      </ul>
    </main>
  );
}
