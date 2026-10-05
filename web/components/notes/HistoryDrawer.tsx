"use client";

import { useEffect, useState } from "react";
import { Check, History, Pencil, Pin, PinOff, X } from "lucide-react";
import { api } from "@/lib/admin";

export type PastMessage = { role: string; content: string; citations?: any[] };
export type PastConv = {
  id: string; title: string; pinned: boolean; created_at: string;
};

/** Past-conversations drawer: AI title (editable) + pin; picking one
 *  loads its full history so the user can continue it. */
export default function HistoryDrawer({
  ws,
  agentId,
  open,
  onClose,
  onPick,
}: {
  ws: string;
  agentId: string;
  open: boolean;
  onClose: () => void;
  onPick: (convId: string, messages: PastMessage[], title: string) => void;
}) {
  const [convs, setConvs] = useState<PastConv[]>([]);
  const [fallbacks, setFallbacks] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [loadingId, setLoadingId] = useState("");
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  async function load() {
    if (!ws || !agentId) return;
    setLoading(true);
    setError("");
    try {
      const list: any[] = await api(
        `/workspaces/${ws}/agents/${agentId}/conversations`
      );
      setConvs(list.map((c: any) => ({
        id: c.id,
        title: c.title ?? "",
        pinned: !!c.pinned,
        created_at: c.created_at ?? "",
      })));
      // fallback titles from first user message (only where AI title empty)
      const needy = list.filter((c: any) => !(c.title ?? "").trim()).slice(0, 30);
      const fb: Record<string, string> = {};
      await Promise.all(needy.map(async (c: any) => {
        try {
          const msgs: any[] = await api(
            `/workspaces/${ws}/conversations/${c.id}/messages`
          );
          const first = msgs.find((m) => m.role === "user" && m.content?.trim());
          fb[c.id] = first
            ? first.content.slice(0, 42) + (first.content.length > 42 ? "…" : "")
            : "گفتگو";
        } catch {
          fb[c.id] = "گفتگو";
        }
      }));
      setFallbacks(fb);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ws, agentId]);

  async function pick(id: string) {
    setLoadingId(id);
    setError("");
    try {
      const msgs: any[] = await api(`/workspaces/${ws}/conversations/${id}/messages`);
      const conv = convs.find((c) => c.id === id);
      onPick(
        id,
        msgs
          .filter((m) => (m.role === "user" || m.role === "assistant") && (m.content ?? "") !== "")
          .map((m) => ({
            role: m.role,
            content: m.content,
            citations: m.citations?.citations ?? [],
          })),
        conv?.title || fallbacks[id] || ""
      );
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoadingId("");
    }
  }

  async function saveTitle(id: string) {
    setError("");
    try {
      const r: any = await api(
        `/workspaces/${ws}/agents/${agentId}/conversations/${id}`,
        { method: "PATCH", json: { title: draft.trim() } }
      );
      setConvs((cs) => cs.map((c) => (c.id === id ? { ...c, title: r.title ?? draft } : c)));
      setEditingId("");
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  async function togglePin(id: string, pinned: boolean) {
    setError("");
    try {
      const r: any = await api(
        `/workspaces/${ws}/agents/${agentId}/conversations/${id}`,
        { method: "PATCH", json: { pinned: !pinned } }
      );
      setConvs((cs) => {
        const next = cs.map((c) => (c.id === id ? { ...c, pinned: !!r.pinned } : c));
        next.sort((a, b) => Number(b.pinned) - Number(a.pinned));
        return next;
      });
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="absolute inset-y-0 right-0 flex w-full max-w-sm flex-col bg-white shadow-xl">
        <div className="flex items-center justify-between border-b p-4">
          <h2 className="flex items-center gap-2 text-sm font-extrabold">
            <History className="h-4 w-4 text-amber-700" />
            گفتگوهای قبلی
          </h2>
          <button onClick={onClose} className="rounded-full p-1 hover:bg-slate-100" aria-label="بستن">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3">
          {!ws || !agentId ? (
            <p className="p-2 text-xs text-slate-400">اول دستیار یادداشت را بساز.</p>
          ) : (
            <>
              {loading && <p className="p-2 text-xs text-slate-400">در حال بارگذاری…</p>}
              {error && <p className="p-2 text-xs text-red-600">{error}</p>}
              {!loading && convs.length === 0 && !error && (
                <p className="p-2 text-xs text-slate-400">هنوز گفتگویی نیست.</p>
              )}
              <ul className="space-y-1.5">
                {convs.map((c) => (
                  <li
                    key={c.id}
                    className={`rounded-2xl border p-3 transition ${c.pinned ? "border-amber-300 bg-amber-50/50" : "hover:bg-amber-50"}`}
                  >
                    {editingId === c.id ? (
                      <div className="flex gap-1">
                        <input
                          autoFocus
                          className="min-w-0 flex-1 rounded-xl border px-2 py-1 text-xs"
                          value={draft}
                          maxLength={80}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void saveTitle(c.id);
                            if (e.key === "Escape") setEditingId("");
                          }}
                        />
                        <button
                          onClick={() => saveTitle(c.id)}
                          className="rounded-xl bg-amber-600 px-2 text-white"
                          aria-label="ذخیره عنوان"
                        >
                          <Check className="h-4 w-4" />
                        </button>
                      </div>
                    ) : (
                      <button onClick={() => pick(c.id)} disabled={!!loadingId} className="w-full text-right disabled:opacity-50">
                        <p className="flex items-center gap-1 truncate text-xs font-bold">
                          {c.pinned && <Pin className="h-3 w-3 shrink-0 text-amber-600" />}
                          <span className="truncate">{c.title || fallbacks[c.id] || "…"}</span>
                        </p>
                        <p className="mt-0.5 text-[11px] text-slate-400" dir="ltr">
                          {loadingId === c.id ? "در حال باز شدن…" : c.created_at.slice(0, 10)}
                        </p>
                      </button>
                    )}
                    {editingId !== c.id && (
                      <div className="mt-1 flex gap-1">
                        <button
                          onClick={() => { setEditingId(c.id); setDraft(c.title || fallbacks[c.id] || ""); }}
                          className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                        >
                          <Pencil className="h-3 w-3" /> ویرایش عنوان
                        </button>
                        <button
                          onClick={() => togglePin(c.id, c.pinned)}
                          className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                        >
                          {c.pinned ? <PinOff className="h-3 w-3" /> : <Pin className="h-3 w-3" />}
                          {c.pinned ? "برداشتن پین" : "پین"}
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
