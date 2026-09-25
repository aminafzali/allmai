import SiteNav from "@/components/SiteNav";
import LoginForm from "@/components/LoginForm";
import { BrainCircuit } from "lucide-react";

export default function StudentLogin() {
  return (
    <main className="min-h-screen bg-gradient-to-b from-green-50/60 to-background">
      <SiteNav />
      <div className="mx-auto max-w-sm px-4 py-14">
        <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
          <BrainCircuit className="h-4 w-4 text-green-600" />
          نسخه دانش‌آموز — مربی و برنامه تحصیلی
        </div>
        <LoginForm
          title="ورود دانش‌آموز"
          subtitle="مربی تحصیلی و برنامه هفتگی شخصی"
          redirectTo="/student"
        />
      </div>
    </main>
  );
}
