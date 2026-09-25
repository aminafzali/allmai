"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function ConversationsPage() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [convs, setConvs] = useState<any[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else {
      setAgents([]);
      setAgentId("");
    }
  }, [ws]);

  useEffect(() => {
    if (ws && agentId)
      api(`/workspaces/${ws}/agents/${agentId}/conversations`)
        .then(setConvs)
        .catch((e: Error) => setError(String(e)));
    else setConvs([]);
    setMessages([]);
  }, [ws, agentId]);

  async function openConv(id: string) {
    setError("");
    try {
      setMessages(await api(`/workspaces/${ws}/conversations/${id}/messages`));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">گفتگوها</h1>
      <div className="mt-4 flex gap-2">
        <WorkspaceSelect value={ws} onChange={setWs} />
        <select
          className="rounded border px-2 py-1"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        >
          <option value="">— انتخاب ایجنت —</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name || a.key}
            </option>
          ))}
        </select>
      </div>
      <FormError e={error} />
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <ul className="space-y-2">
          {convs.map((c) => (
            <li key={c.id} className="rounded border bg-white p-3 text-sm">
              <div dir="ltr" className="text-xs text-slate-500">{c.id.slice(0, 8)}…</div>
              <div className="text-xs text-slate-500">turns: {c.state?.turns ?? 0}</div>
              <button className="mt-1 text-blue-700 hover:underline" onClick={() => openConv(c.id)}>
                مشاهده پیام‌ها
              </button>
            </li>
          ))}
          {agentId && convs.length === 0 && <p className="text-sm text-slate-500">گفتگویی نیست.</p>}
        </ul>
        <div className="space-y-2">
          {messages.map((m, i) => (
            <div
              key={i}
              className={`rounded p-3 text-sm ${m.role === "user" ? "bg-blue-50" : "bg-white border"}`}
            >
              <strong className="text-xs text-slate-500">{m.role}</strong>
              <p className="mt-1 whitespace-pre-wrap">{m.content.slice(0, 2000)}</p>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
