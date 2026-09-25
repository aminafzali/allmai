"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function TeacherHome() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [error, setError] = useState("");

  async function load(v: string) {
    setWs(v);
    if (!v) {
      setAgents([]);
      return;
    }
    try {
      setAgents(await api(`/workspaces/${v}/agents`));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">خانه معلم</h1>
      <div className="mt-4">
        <WorkspaceSelect value={ws} onChange={load} />
      </div>
      <FormError e={error} />
      {ws && (
        <ul className="mt-4 space-y-2">
          {agents
            .filter((a) => a.key === "teacher_lesson_planner")
            .map((a) => (
              <li key={a.id} className="rounded border bg-white p-3">
                <strong>{a.name || a.key}</strong>
                <div className="mt-1 flex gap-3 text-sm">
                  <Link className="text-blue-700 hover:underline" href="/teacher/lesson-plans">
                    طرح درس جدید
                  </Link>
                  <Link className="text-blue-700 hover:underline" href="/teacher/chat">
                    گفتگو
                  </Link>
                </div>
              </li>
            ))}
          {agents.filter((a) => a.key === "teacher_lesson_planner").length === 0 && (
            <p className="text-sm text-slate-500">
              ایجنت طرح درس در این ورک‌اسپیس نیست — از پنل ادمین بسازید.
            </p>
          )}
        </ul>
      )}
    </main>
  );
}
