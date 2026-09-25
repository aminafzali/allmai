"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function Progress() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [convs, setConvs] = useState<any[]>([]);
  const [convId, setConvId] = useState("");
  const [plan, setPlan] = useState<any>(null);
  const [done, setDone] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  useEffect(() => {
    const coach = agents.find((a) => a.key === "student_academic_coach");
    if (coach) setAgentId(coach.id);
  }, [agents]);

  useEffect(() => {
    if (ws && agentId)
      api(`/workspaces/${ws}/agents/${agentId}/conversations`).then(setConvs).catch(() => {});
    else setConvs([]);
  }, [ws, agentId]);

  useEffect(() => {
    const c = convs.find((x) => x.id === convId);
    if (c?.state?.plan) {
      setPlan(c.state.plan);
      setDone(c.state.progress?.completed ?? []);
    } else {
      setPlan(null);
      setDone([]);
    }
  }, [convId, convs]);

  function toggle(id: string) {
    setDone((d) => (d.includes(id) ? d.filter((x) => x !== id) : [...d, id]));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const r = await api(`/workspaces/${ws}/agents/${agentId}/progress`, {
        method: "POST",
        json: { conversation_id: convId, completed_task_ids: done, note },
      });
      setDone(r.completed_task_ids);
      setNote("");
      setConvs(await api(`/workspaces/${ws}/agents/${agentId}/conversations`));
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  const total = plan?.weeks?.reduce((n: number, w: any) => n + (w.tasks?.length ?? 0), 0) ?? 0;

  return (
    <main>
      <h1 className="text-xl font-bold">پیشرفت</h1>
      <div className="mt-4 flex gap-2">
        <WorkspaceSelect value={ws} onChange={setWs} />
        <select className="rounded border px-2 py-1" value={convId} onChange={(e) => setConvId(e.target.value)}>
          <option value="">— انتخاب برنامه —</option>
          {convs.filter((c) => c.state?.plan).map((c) => (
            <option key={c.id} value={c.id}>
              {(c.state.plan.weeks?.[0]?.focus ?? "برنامه").slice(0, 40)}… ({(c.state.progress?.completed?.length ?? 0)}/{c.state.plan.weeks?.reduce((n: number, w: any) => n + (w.tasks?.length ?? 0), 0) ?? 0})
            </option>
          ))}
        </select>
      </div>
      <FormError e={error} />
      {plan && (
        <form onSubmit={save} className="mt-4">
          <p className="text-sm text-slate-600">{done.length} از {total} تسک انجام شده</p>
          <div className="mt-2 space-y-2">
            {plan.weeks.flatMap((w: any) => w.tasks ?? []).map((t: any) => (
              <label key={t.id} className="flex items-center gap-2 rounded border bg-white p-2 text-sm">
                <input type="checkbox" checked={done.includes(t.id)} onChange={() => toggle(t.id)} />
                <span className={done.includes(t.id) ? "line-through text-slate-400" : ""}>{t.title}</span>
              </label>
            ))}
          </div>
          <input className="mt-3 w-full rounded border px-3 py-1" placeholder="یادداشت (اختیاری)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="mt-2 rounded bg-green-700 px-4 py-2 text-white" type="submit">
            ثبت پیشرفت
          </button>
        </form>
      )}
    </main>
  );
}
