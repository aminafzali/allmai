"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Check, ChevronDown, Files, History, MessageSquarePlus } from "lucide-react";
import { api } from "@/lib/admin";
import { getNotesWs, setNotesWs, NOTES_WS_KEY, clearNotesConvs } from "@/lib/notes";

/** Notes app shell: sticky top bar (workspace switcher + icon nav),
 *  bottom app-like nav on mobile. Single nav — no duplicates. */
export default function NotesLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [spaces, setSpaces] = useState<any[]>([]);
  const [ws, setWs] = useState("");
  const [wsOpen, setWsOpen] = useState(false);

  useEffect(() => {
    setWs(getNotesWs());
    api("/workspaces").then(setSpaces).catch(() => {});
  }, []);

  function switchWs(id: string) {
    if (!id || id === ws) {
      setWsOpen(false);
      return;
    }
    setNotesWs(id);
    clearNotesConvs();
    setWs(id);
    setWsOpen(false);
    router.push("/notes/chat");
  }

  const wsName = spaces.find((w) => w.id === ws)?.name ?? "";

  return (
    <div className="min-h-dvh bg-gradient-to-b from-amber-50/60 via-slate-50 to-slate-50 pb-20 md:pb-8">
      <header className="sticky top-0 z-30 border-b bg-white/85 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-3xl items-center gap-2 px-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-amber-600 text-lg text-white">
            ✎
          </span>
          <div className="relative">
            <button
              onClick={() => setWsOpen((o) => !o)}
              title="تعویض ورک‌اسپیس"
              className="flex max-w-44 items-center gap-1 rounded-full px-2 py-1 transition hover:bg-slate-900/5"
            >
              <span className="truncate text-sm font-extrabold">
                {wsName || "ورک‌اسپیس"}
              </span>
              <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition ${wsOpen ? "rotate-180" : ""}`} />
            </button>
            {wsOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setWsOpen(false)} />
                <div className="absolute right-0 z-20 mt-1 max-h-64 w-56 overflow-auto rounded-2xl border bg-white py-1 shadow-xl">
                  {spaces.map((w) => (
                    <button
                      key={w.id}
                      onClick={() => switchWs(w.id)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-right text-xs hover:bg-slate-50 ${w.id === ws ? "font-bold text-amber-700" : ""}`}
                    >
                      <span className="min-w-0 flex-1 truncate">{w.name}</span>
                      {w.id === ws && <Check className="h-3.5 w-3.5 shrink-0" />}
                    </button>
                  ))}
                  {spaces.length === 0 && (
                    <p className="px-3 py-2 text-xs text-slate-400">ورک‌اسپیسی نیست</p>
                  )}
                </div>
              </>
            )}
          </div>
          <nav className="ms-auto hidden items-center gap-1 text-sm text-slate-500 md:flex">
            <Link href="/notes/chat" className="flex items-center gap-1.5 rounded-full px-3 py-1.5 transition hover:bg-slate-900/5 hover:text-slate-900">
              <MessageSquarePlus className="h-4 w-4" />
              گفتگو
            </Link>
            <Link href="/notes/files" className="flex items-center gap-1.5 rounded-full px-3 py-1.5 transition hover:bg-slate-900/5 hover:text-slate-900">
              <Files className="h-4 w-4" />
              فایل‌ها
            </Link>
            <Link href="/notes/chat?history=1" className="flex items-center gap-1.5 rounded-full px-3 py-1.5 transition hover:bg-slate-900/5 hover:text-slate-900">
              <History className="h-4 w-4" />
              گفتگوهای قبلی
            </Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl px-4 pt-4">{children}</div>

      {/* mobile bottom nav — Android-app feel */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <div className="grid grid-cols-3 text-[11px]">
          <Link href="/notes/chat" className="flex flex-col items-center gap-0.5 py-2 text-slate-600 active:bg-slate-100">
            <MessageSquarePlus className="h-5 w-5" />
            گفتگو
          </Link>
          <Link href="/notes/files" className="flex flex-col items-center gap-0.5 py-2 text-slate-600 active:bg-slate-100">
            <Files className="h-5 w-5" />
            فایل‌ها
          </Link>
          <Link href="/notes/chat?history=1" className="flex flex-col items-center gap-0.5 py-2 text-slate-600 active:bg-slate-100">
            <History className="h-5 w-5" />
            گفتگوهای قبلی
          </Link>
        </div>
      </nav>
    </div>
  );
}
