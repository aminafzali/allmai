"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/admin";
import { FormError, useWorkspaces } from "@/components/Selectors";

export default function WorkspacesPage() {
  const { items, error, reload } = useWorkspaces();
  const [name, setName] = useState("");
  const [type, setType] = useState("shared");
  const [formError, setFormError] = useState("");

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    try {
      await api("/workspaces", { method: "POST", json: { name, type } });
      setName("");
      reload();
    } catch (err: any) {
      setFormError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">ورک‌اسپیس‌ها</h1>
      <form onSubmit={create} className="mt-4 flex gap-2">
        <input
          className="rounded border px-3 py-1"
          placeholder="نام ورک‌اسپیس"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select className="rounded border px-2 py-1" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="shared">مشترک</option>
          <option value="personal">شخصی</option>
        </select>
        <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit">
          بساز
        </button>
      </form>
      <FormError e={formError || error} />
      <ul className="mt-4 space-y-2">
        {items.map((w) => (
          <li key={w.id} className="rounded border bg-white p-3">
            <strong>{w.name}</strong> <span className="text-xs text-slate-500">({w.type})</span>
            <div className="mt-1 text-xs" dir="ltr">{w.id}</div>
            <div className="mt-1 flex gap-3 text-sm">
              <Link className="text-blue-700 hover:underline" href="/admin/knowledge-bases">
                بیس‌های دانش
              </Link>
              <Link className="text-blue-700 hover:underline" href="/admin/sources">
                سورس‌ها
              </Link>
            </div>
          </li>
        ))}
      </ul>
    </main>
  );
}
