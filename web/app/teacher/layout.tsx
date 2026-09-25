import RequireAuth from "@/components/RequireAuth";
import Link from "next/link";

export default function TeacherLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth to="/teacher/login">
      <nav className="flex flex-wrap items-center gap-3 border-b bg-white px-6 py-3">
        <strong className="ml-2">پنل معلم</strong>
        <Link href="/teacher" className="text-sm text-blue-700 hover:underline">خانه</Link>
        <Link href="/teacher/lesson-plans" className="text-sm text-blue-700 hover:underline">
          طرح درس جدید
        </Link>
        <Link href="/teacher/chat" className="text-sm text-blue-700 hover:underline">گفتگو</Link>
        <Link href="/" className="mr-auto text-sm text-slate-500 hover:underline">AllMai</Link>
      </nav>
      <div className="mx-auto max-w-4xl p-6">{children}</div>
    </RequireAuth>
  );
}
