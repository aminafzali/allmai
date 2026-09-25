"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function StudyPlan() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [goals, setGoals] = useState("");
  const [hours, setHours] = useState(10);
  const [plan, setPlan] = useState<any>(null);
  const [convId, setConvId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  useEffect(() => {
    const coach = agents.find((a) => a.key === "student_academic_coach");
    if (coach) setAgentId(coach.id);
  }, [agents]);

  async function loadLatest(aid: string) {
    try {
      const convs = await api(`/workspaces/${ws}/agents/${aid}/conversations`);
      const withPlan = convs.find((c: any) => c.state?.plan);
      if (withPlan) {
        setPlan(withPlan.state.plan);
        setConvId(withPlan.id);
      }
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (ws && agentId) loadLatest(agentId);
  }, [ws, agentId]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await api(`/workspaces/${ws}/agents/${agentId}/study-plans`, {
        method: "POST",
        json: { goals: goals.split("\n").map((g) => g.trim()).filter(Boolean), weekly_hours: Number(hours) },
      });
      setPlan(r.plan);
      setConvId(r.conversation_id);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">برنامه تحصیلی</h1>
      <div className="mt-4">
        <WorkspaceSelect value={ws} onChange={setWs} />
      </div>
      {agentId && (
        <form onSubmit={create} className="mt-4 flex max-w-xl flex-col gap-2">
          <textarea className="rounded border px-3 py-1" rows={3} placeholder="اهداف (هر خط یکی)" value={goals} onChange={(e) => setGoals(e.target.value)} />
          <input className="rounded border px-3 py-1" type="number" placeholder="ساعت در هفته" value={hours} onChange={(e) => setHours(Number(e.target.value))} />
          <button className="rounded bg-blue-700 px-4 py-2 text-white" type="submit" disabled={busy}>
            {busy ? "در حال ساخت…" : "ساخت برنامه"}
          </button>
        </form>
      )}
      <FormError e={error} />
      {plan && (
        <div className="mt-4 space-y-3">
          {plan.weeks.map((w: any) => (
            <section key={w.week} className="rounded border bg-white p-3 text-sm">
              <h2 className="font-bold">هفته {w.week}: {w.focus}</h2>
              <ul className="mt-1 space-y-1">
                {(w.tasks ?? []).map((t: any) => (
                  <li key={t.id} className="flex justify-between gap-2">
                    <span>{t.title} <span className="text-xs text-slate-500">({t.subject})</span></span>
                    <span className="text-xs text-slate-500">{t.minutes}′</span>
                  </li>
                ))}
              </ul>
              {(w.milestones ?? []).length > 0 && (
                <p className="mt-1 text-xs text-slate-600">🎯 {(w.milestones ?? []).join("؛ ")}</p>
              )}
            </section>
          ))}
          {(plan.advice ?? []).length > 0 && (
            <section className="rounded border bg-white p-3 text-sm">
              <h2 className="font-bold">توصیه‌ها</h2>
              <ul className="list-disc pr-5">{plan.advice.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
            </section>
          )}
        </div>
      )}
    </main>
  );
}
