"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError, WorkspaceSelect } from "@/components/Selectors";

export default function StudentHome() {
  const [ws, setWs] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [profile, setProfile] = useState<any>(null);
  const [grade, setGrade] = useState("");
  const [weak, setWeak] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  const coach = agents.find((a) => a.key === "student_academic_coach");

  useEffect(() => {
    if (ws && coach) {
      setAgentId(coach.id);
      api(`/workspaces/${ws}/agents/${coach.id}/profile`).then(setProfile).catch(() => {});
    }
  }, [ws, agents]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(`/workspaces/${ws}/agents/${agentId}/profile`, {
        method: "POST",
        json: { facts: { grade, weak_subject: weak } },
      });
      setProfile(await api(`/workspaces/${ws}/agents/${agentId}/profile`));
    } catch (err: any) {
      setError(String(err?.message ?? err));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">خانه دانش‌آموز</h1>
      <div className="mt-4">
        <WorkspaceSelect value={ws} onChange={setWs} />
      </div>
      <FormError e={error} />
      {coach && (
        <>
          <section className="mt-4 rounded border bg-white p-4">
            <h2 className="font-bold">پروفایل تحصیلی</h2>
            {profile && (
              <pre className="mt-2 rounded bg-slate-100 p-2 text-xs" dir="ltr">
                {JSON.stringify(profile, null, 2)}
              </pre>
            )}
            <form onSubmit={save} className="mt-2 flex flex-wrap gap-2">
              <input className="rounded border px-3 py-1" placeholder="پایه" value={grade} onChange={(e) => setGrade(e.target.value)} />
              <input className="rounded border px-3 py-1" placeholder="درس ضعیف" value={weak} onChange={(e) => setWeak(e.target.value)} />
              <button className="rounded bg-blue-700 px-4 py-1 text-white" type="submit">ذخیره</button>
            </form>
          </section>
          <div className="mt-3 flex gap-3 text-sm">
            <a className="text-blue-700 hover:underline" href="/student/chat">مربی</a>
            <a className="text-blue-700 hover:underline" href="/student/plan">برنامه</a>
            <a className="text-blue-700 hover:underline" href="/student/progress">پیشرفت</a>
          </div>
        </>
      )}
      {ws && !coach && <p className="mt-3 text-sm text-slate-500">مربی تحصیلی در این ورک‌اسپیس نیست.</p>}
    </main>
  );
}
