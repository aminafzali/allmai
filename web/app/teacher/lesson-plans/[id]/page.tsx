"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

export default function LessonPlanDetail({ params }: { params: { id: string } }) {
  const [detail, setDetail] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ws = localStorage.getItem("allmai_last_ws") ?? "";
    if (!ws) {
      setError("ورک‌اسپیس مشخص نیست — از فهرست طرح‌ها باز کنید.");
      setLoading(false);
      return;
    }
    api(`/workspaces/${ws}/lesson-plans/by-conversation/${params.id}`)
      .then(setDetail)
      .catch((e: Error) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [params.id]);

  if (loading) return <main><p className="p-4 text-sm text-slate-500">در حال بارگذاری…</p></main>;

  const parsed = detail?.plan ?? null;

  return (
    <main>
      <h1 className="text-xl font-bold">جزئیات طرح درس</h1>
      <FormError e={error} />
      {parsed ? (
        <div className="mt-4 space-y-4 text-sm">
          <p className="text-xs text-slate-500">
            {detail.chapter} · پایه {detail.grade} · {detail.duration_minutes} دقیقه · {detail.teaching_style}
          </p>
          <h2 className="text-lg font-bold">{parsed.title}</h2>
          {parsed.objectives && (
            <section><h3 className="font-bold">اهداف</h3><ul className="list-disc pr-5">{parsed.objectives.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.prerequisites && (
            <section><h3 className="font-bold">پیش‌نیازها</h3><ul className="list-disc pr-5">{parsed.prerequisites.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.topics && (
            <section><h3 className="font-bold">مباحث</h3>{parsed.topics.map((t: any, i: number) => (
              <div key={i} className="mt-1 rounded border bg-white p-2">
                <strong>{t.title}</strong>
                <ul className="list-disc pr-5">{(t.points ?? []).map((p: string, j: number) => <li key={j}>{p}</li>)}</ul>
              </div>
            ))}</section>
          )}
          {parsed.activities && (
            <section><h3 className="font-bold">فعالیت‌ها</h3><ul className="list-disc pr-5">{parsed.activities.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.examples && (
            <section><h3 className="font-bold">مثال‌ها</h3><ul className="list-disc pr-5">{parsed.examples.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.questions && (
            <section><h3 className="font-bold">سؤالات</h3><ul className="list-disc pr-5">{parsed.questions.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.assessment && (
            <section><h3 className="font-bold">ارزشیابی</h3><ul className="list-disc pr-5">{parsed.assessment.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul></section>
          )}
          {parsed.references && (
            <section><h3 className="font-bold">منابع دانش</h3><ul className="list-disc pr-5 text-xs text-slate-600">
              {parsed.references.map((r: any) => (
                <li key={r.n} dir="ltr">[{r.n}] {r.source} {r.page_no ? `p.${r.page_no}` : ""}{r.start_ms != null ? ` ${r.start_ms}-${r.end_ms}ms` : ""}</li>
              ))}
            </ul></section>
          )}
        </div>
      ) : (
        !error && <p className="mt-4 text-sm text-slate-500">طرحی یافت نشد.</p>
      )}
    </main>
  );
}
