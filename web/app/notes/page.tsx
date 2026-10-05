"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FolderOpen, MessageSquarePlus, Plus } from "lucide-react";
import { api } from "@/lib/admin";
import { NOTES_WS_KEY } from "@/lib/notes";
import { FormError } from "@/components/Selectors";

const GRADS = [
  "from-blue-100 to-sky-50",
  "from-green-100 to-emerald-50",
  "from-amber-100 to-yellow-50",
  "from-violet-100 to-purple-50",
  "from-rose-100 to-pink-50",
  "from-teal-100 to-cyan-50",
];

export default function NotesHome() {
  const router = useRouter();
  const [spaces, setSpaces] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/workspaces")
      .then(setSpaces)
      .catch((e: Error) => setError(String(e?.message ?? e)));
  }, []);

  useEffect(() => {
    if (picked) api(`/workspaces/${picked}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [picked]);

  const assistant = agents.find((a) => a.key === "note_taking_assistant");

  function choose(id: string) {
    setPicked(id);
    setError("");
  }

  function goChat() {
    if (!picked) return;
    try {
      localStorage.setItem(NOTES_WS_KEY, picked);
    } catch {
      /* storage unavailable */
    }
    router.push("/notes/chat");
  }

  async function create() {
    if (!picked) return;
    setError("");
    setBusy(true);
    try {
      await api(`/workspaces/${picked}/agents`, {
        method: "POST",
        json: { key: "note_taking_assistant", type: "agent", name: "دستیار یادداشت" },
      });
      setAgents(await api(`/workspaces/${picked}/agents`));
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {!picked ? (
        <>
          <h1 className="text-lg font-extrabold">یک ورک‌اسپیس انتخاب کن</h1>
          <p className="mt-1 text-xs text-slate-500">
            فایل‌ها و گفتگوهای هر ورک‌اسپیس جدا می‌ماند.
          </p>
          <FormError e={error} />
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {spaces.map((w: any, i: number) => (
              <button
                key={w.id}
                onClick={() => choose(w.id)}
                className={`flex items-center gap-3 rounded-3xl border bg-gradient-to-bl p-4 text-right shadow-sm transition hover:shadow active:scale-[0.99] ${GRADS[i % GRADS.length]}`}
              >
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white/80 shadow-sm">
                  <FolderOpen className="h-5 w-5 text-amber-700" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-extrabold">{w.name}</span>
                  <span className="mt-0.5 block text-[11px] text-slate-500">{w.type}</span>
                </span>
                <ArrowLeft className="h-4 w-4 shrink-0 text-slate-400" />
              </button>
            ))}
          </div>
          {spaces.length === 0 && !error && (
            <p className="mt-6 text-center text-xs text-slate-400">در حال بارگذاری…</p>
          )}
        </>
      ) : (
        <>
          <button onClick={() => setPicked("")} className="text-xs text-slate-500 hover:text-slate-700">
            → انتخاب ورک‌اسپیس دیگر
          </button>
          <div className="mt-2 rounded-3xl border bg-white p-5 text-center shadow-sm">
            {!assistant ? (
              <>
                <p className="text-sm font-extrabold">دستیار یادداشت برای این ورک‌اسپیس ساخته نشده</p>
                <button
                  className="mt-3 w-full rounded-2xl bg-amber-600 py-2.5 text-sm font-bold text-white disabled:opacity-50"
                  onClick={create}
                  disabled={busy}
                >
                  {busy ? "…" : "ساخت دستیار یادداشت"}
                </button>
              </>
            ) : (
              <>
                <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100">
                  <MessageSquarePlus className="h-6 w-6 text-amber-700" />
                </span>
                <p className="mt-2 text-sm font-extrabold">همه‌چیز آماده است</p>
                <button
                  className="mt-3 flex w-full items-center justify-center gap-1 rounded-2xl bg-amber-600 py-2.5 text-sm font-bold text-white"
                  onClick={goChat}
                >
                  ورود به گفتگو <ArrowLeft className="h-4 w-4" />
                </button>
                <Link
                  href="/notes/files"
                  onClick={() => {
                    try {
                      localStorage.setItem(NOTES_WS_KEY, picked);
                    } catch {
                      /* ignore */
                    }
                  }}
                  className="mt-2 flex w-full items-center justify-center gap-1 rounded-2xl border py-2.5 text-sm"
                >
                  <Plus className="h-4 w-4" /> فایل‌ها
                </Link>
              </>
            )}
          </div>
          <FormError e={error} />
        </>
      )}
    </div>
  );
}

