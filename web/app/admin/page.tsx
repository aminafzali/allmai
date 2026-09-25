"use client";

import { useEffect, useState } from "react";
import { api, apiHealth } from "@/lib/admin";

export default function AdminHome() {
  const [health, setHealth] = useState("…");
  const [models, setModels] = useState<any>(null);
  useEffect(() => {
    apiHealth().then((h) => setHealth(h.status)).catch(() => setHealth("down"));
    api("/ai/providers").then(setModels).catch(() => {});
  }, []);
  return (
    <main>
      <h1 className="text-2xl font-bold">داشبورد</h1>
      <p className="mt-2">وضعیت API: <strong>{health}</strong></p>
      {models && (
        <pre className="mt-4 overflow-auto rounded bg-slate-100 p-3 text-xs" dir="ltr">
          {JSON.stringify(models, null, 2)}
        </pre>
      )}
    </main>
  );
}
