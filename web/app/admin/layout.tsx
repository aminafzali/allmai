import RequireAuth from "@/components/RequireAuth";
import AdminSidebar from "@/components/AdminSidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth to="/admin/login">
      {/* در حالت راست‌به‌چپ، سایدبار (اولین فرزند) سمت راست قرار می‌گیرد */}
      <div className="flex min-h-screen bg-muted/40">
        <AdminSidebar />
        <div className="mx-auto w-full max-w-5xl flex-1 p-4 md:p-6">{children}</div>
      </div>
    </RequireAuth>
  );
}
