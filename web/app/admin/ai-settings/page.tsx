"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

export default function AISettingsPage() {
  const [settings, setSettings] = useState<Record<string, any>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const reload = () =>
    api("/admin/ai-settings")
      .then((s) => {
        setSettings(s);
        setDrafts(Object.fromEntries(Object.entries(s).map(([k, v]) => [k, JSON.stringify(v, null, 2)])));
      })
      .catch((e: Error) => setError(String(e)));
  useEffect(() => {
    reload();
  }, []);

  async function save(key: string) {
    setError("");
    setSaved("");
    try {
      await api("/admin/ai-settings", {
        method: "PUT",
        json: { key, value: JSON.parse(drafts[key]) },
      });
      setSaved(`ذخیره شد: ${key}`);
      reload();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">تنظیمات مدل</h1>
      <p className="mt-1 text-sm text-slate-600">
        این مقادیر روی ENV ارجح‌اند و بلافاصله روی ایجنت‌ها اثر می‌گذارند.
      </p>
      <FormError e={error} />
      {saved && <p className="mt-2 text-sm text-green-700">{saved}</p>}
      <div className="mt-4 space-y-4">
        {Object.keys(settings).map((key) => (
          <section key={key} className="rounded border bg-white p-4">
            <h2 className="font-bold" dir="ltr">{key}</h2>
            <textarea
              className="mt-2 w-full rounded border bg-slate-50 p-2 font-mono text-xs"
              rows={6}
              dir="ltr"
              value={drafts[key] ?? ""}
              onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })}
            />
            <button
              className="mt-2 rounded bg-blue-700 px-4 py-1 text-sm text-white"
              onClick={() => save(key)}
            >
              ذخیره
            </button>
          </section>
        ))}
      </div>
    </main>
  );
}
