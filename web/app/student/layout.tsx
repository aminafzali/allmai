import RequireAuth from "@/components/RequireAuth";
import Link from "next/link";

export default function StudentLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth to="/student/login">
      <nav className="flex flex-wrap items-center gap-3 border-b bg-white px-6 py-3">
        <strong className="ml-2">پنل دانش‌آموز</strong>
        <Link href="/student" className="text-sm text-blue-700 hover:underline">خانه</Link>
        <Link href="/student/chat" className="text-sm text-blue-700 hover:underline">مربی</Link>
        <Link href="/student/plan" className="text-sm text-blue-700 hover:underline">برنامه</Link>
        <Link href="/student/progress" className="text-sm text-blue-700 hover:underline">پیشرفت</Link>
        <Link href="/" className="mr-auto text-sm text-slate-500 hover:underline">AllMai</Link>
      </nav>
      <div className="mx-auto max-w-4xl p-6">{children}</div>
    </RequireAuth>
  );
}
