"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Database } from "lucide-react";
import { api } from "@/lib/admin";

type KB = { id: string; title: string };

/** Modern knowledge-base dropdown (labelled «پایگاه دانش»). */
export default function KbPicker({
  ws,
  value,
  onChange,
}: {
  ws: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const [kbs, setKbs] = useState<KB[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/knowledge-bases`).then(setKbs).catch(() => {});
    else setKbs([]);
  }, [ws]);

  const title = kbs.find((k) => k.id === value)?.title ?? "";

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-2xl border bg-white px-3 py-2 text-xs shadow-sm"
      >
        <Database className="h-4 w-4 text-amber-700" />
        <span className="text-slate-400">پایگاه دانش:</span>
        <strong className="max-w-40 truncate">{title || "همه"}</strong>
        <ChevronDown className={`h-3.5 w-3.5 text-slate-400 transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 max-h-56 w-56 overflow-auto rounded-2xl border bg-white py-1 shadow-xl">
            <button
              onClick={() => { onChange(""); setOpen(false); }}
              className={`block w-full px-3 py-2 text-right text-xs hover:bg-slate-50 ${!value ? "font-bold text-amber-700" : ""}`}
            >
              همه پایگاه‌ها
            </button>
            {kbs.map((k) => (
              <button
                key={k.id}
                onClick={() => { onChange(k.id); setOpen(false); }}
                className={`block w-full truncate px-3 py-2 text-right text-xs hover:bg-slate-50 ${value === k.id ? "font-bold text-amber-700" : ""}`}
              >
                {k.title}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
