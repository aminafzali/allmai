"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

export default function AgentStudioPage() {
  const [items, setItems] = useState<any[]>([]);
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");

  const reload = () => {
    api("/admin/agent-definitions").then(setItems).catch((e: Error) => setError(String(e)));
  };
  useEffect(reload, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/admin/agent-definitions", {
        method: "POST",
        json: { key, title: title || key, instructions },
      });
      setKey("");
      setTitle("");
      setInstructions("");
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">استودیو ایجنت</h1>
      <p className="mt-1 text-sm text-slate-500">
        تعریف‌های سراسری ایجنت (دستورالعمل، رفتار، ابزار، مدل، ایمنی) و دانش سراسری منتسب به هر تعریف.
      </p>

      <form onSubmit={create} className="mt-4 space-y-2 rounded border bg-white p-4">
        <h2 className="font-bold">تعریف جدید</h2>
        <div className="flex flex-wrap gap-2">
          <input
            className="rounded border px-3 py-1"
            placeholder="key (latin, e.g. fitness_coach)"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            dir="ltr"
          />
          <input
            className="rounded border px-3 py-1"
            placeholder="عنوان"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <textarea
          className="w-full rounded border px-3 py-1"
          rows={3}
          placeholder="دستورالعمل پیش‌فرض…"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
        <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit">
          بساز
        </button>
      </form>
      <FormError e={error} />

      <ul className="mt-4 space-y-2">
        {items.map((d) => (
          <li key={d.id} className="rounded border bg-white p-3 text-sm">
            <Link className="font-bold text-blue-700 hover:underline" href={`/admin/agent-studio/${d.id}`}>
              {d.title || d.key}
            </Link>
            <span className="text-slate-500"> ({d.key} · {d.type})</span>
            {!d.is_active && <span className="mr-2 rounded bg-slate-200 px-2 py-0.5 text-xs">غیرفعال</span>}
            <p className="mt-1 line-clamp-2 text-slate-600">{d.instructions}</p>
          </li>
        ))}
      </ul>
    </main>
  );
}
