"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/admin";
import { FormError, KBSelect, WorkspaceSelect } from "@/components/Selectors";

export default function NewLessonPlan() {
  const router = useRouter();
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [chapter, setChapter] = useState("");
  const [grade, setGrade] = useState("");
  const [duration, setDuration] = useState(45);
  const [style, setStyle] = useState("interactive lecture");
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ws) api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
    else setAgents([]);
  }, [ws]);

  const teachers = agents.filter((a) => a.key === "teacher_lesson_planner");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await api(`/workspaces/${ws}/agents/${agentId}/lesson-plans`, {
        method: "POST",
        json: {
          kb_id: kb,
          chapter,
          grade,
          duration_minutes: Number(duration),
          teaching_style: style,
          instructions,
        },
      });
      localStorage.setItem("allmai_last_ws", ws);
      router.push(`/teacher/lesson-plans/${r.conversation_id}`);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">طرح درس جدید</h1>
      <form onSubmit={submit} className="mt-4 flex max-w-xl flex-col gap-2">
        <WorkspaceSelect value={ws} onChange={(v) => { setWs(v); setKb(""); }} />
        <KBSelect wsId={ws} value={kb} onChange={setKb} />
        <select
          className="rounded border px-2 py-1"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        >
          <option value="">— انتخاب ایجنت طرح درس —</option>
          {teachers.map((a) => (
            <option key={a.id} value={a.id}>{a.name || a.key}</option>
          ))}
        </select>
        <input className="rounded border px-3 py-1" placeholder="فصل / مبحث" value={chapter} onChange={(e) => setChapter(e.target.value)} />
        <input className="rounded border px-3 py-1" placeholder="پایه (مثلاً نهم)" value={grade} onChange={(e) => setGrade(e.target.value)} />
        <input className="rounded border px-3 py-1" placeholder="مدت (دقیقه)" type="number" value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
        <input className="rounded border px-3 py-1" placeholder="سبک تدریس" value={style} onChange={(e) => setStyle(e.target.value)} />
        <textarea className="rounded border px-3 py-1" placeholder="دستور اضافه معلم (اختیاری)" rows={3} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        <button className="rounded bg-blue-700 px-4 py-2 text-white" type="submit" disabled={busy || !ws || !kb || !agentId}>
          {busy ? "در حال ساخت…" : "ساخت طرح درس"}
        </button>
      </form>
      <FormError e={error} />
    </main>
  );
}
