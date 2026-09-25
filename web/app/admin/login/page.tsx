import SiteNav from "@/components/SiteNav";
import LoginForm from "@/components/LoginForm";
import { ShieldCheck } from "lucide-react";

export default function AdminLogin() {
  return (
    <main className="min-h-screen bg-muted/40">
      <SiteNav />
      <div className="mx-auto max-w-sm px-4 py-14">
        <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
          <ShieldCheck className="h-4 w-4" />
          ناحیه مدیریت — فقط مدیران سیستم
        </div>
        <LoginForm
          title="ورود ادمین"
          subtitle="مدیریت کاربران، دانش، ایجنت‌ها و تنظیمات مدل"
          redirectTo="/admin"
        />
      </div>
    </main>
  );
}
