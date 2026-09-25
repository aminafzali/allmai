"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function LessonPlansList() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [plans, setPlans] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  useEffect(() => {
    if (ws && agentId)
      api(`/workspaces/${ws}/agents/${agentId}/lesson-plans`)
        .then(setPlans)
        .catch((e: Error) => setError(String(e)));
    else setPlans([]);
  }, [ws, agentId]);

  const teachers = agents.filter((a) => a.key === "teacher_lesson_planner");

  function openPlan(convId: string) {
    localStorage.setItem("allmai_last_ws", ws);
    window.location.href = `/teacher/lesson-plans/${convId}`;
  }

  return (
    <main>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">طرح درس‌ها</h1>
        <Link className="rounded bg-blue-700 px-4 py-1 text-sm text-white" href="/teacher/lesson-plans/new">
          طرح جدید
        </Link>
      </div>
      <div className="mt-4 flex gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setAgentId(""); }} />
        <select className="rounded border px-2 py-1" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
          <option value="">— ایجنت طرح درس —</option>
          {teachers.map((a) => (
            <option key={a.id} value={a.id}>{a.name || a.key}</option>
          ))}
        </select>
      </div>
      <FormError e={error} />
      {agentId && plans.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">هنوز طرح درسی ساخته نشده است.</p>
      )}
      <ul className="mt-4 space-y-2">
        {plans.map((p) => (
          <li key={p.id}>
            <button
              className="w-full rounded border bg-white p-3 text-right hover:border-blue-400"
              onClick={() => openPlan(p.conversation_id)}
            >
              <strong>{p.chapter}</strong>
              <span className="mr-2 text-xs text-slate-500">
                پایه {p.grade} · {p.duration_minutes} دقیقه · {p.teaching_style}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
