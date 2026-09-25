import SiteNav from "@/components/SiteNav";
import LoginForm from "@/components/LoginForm";
import { BookOpenCheck } from "lucide-react";

export default function TeacherLogin() {
  return (
    <main className="min-h-screen bg-gradient-to-b from-blue-50/60 to-background">
      <SiteNav />
      <div className="mx-auto max-w-sm px-4 py-14">
        <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
          <BookOpenCheck className="h-4 w-4 text-blue-600" />
          نسخه معلم — طرح درس و دستیار آموزشی
        </div>
        <LoginForm
          title="ورود معلم"
          subtitle="طرح درس هوشمند از منابع خودتان"
          redirectTo="/teacher"
        />
      </div>
    </main>
  );
}
