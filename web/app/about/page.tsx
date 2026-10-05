import Link from "next/link";
import SiteNav from "@/components/SiteNav";
import SiteFooter from "@/components/SiteFooter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/separator-badge";
import { DatabaseZap, MessagesSquare, ShieldCheck, Workflow } from "lucide-react";

const PILLARS = [
  {
    icon: MessagesSquare,
    title: "پاسخ مستند، نه حدس",
    desc: "معماری بازیابی ترکیبی (وکتور + تمام‌متن) با ارجاع دقیق به فایل، صفحه و دقیقه صوت؛ هر ادعا قابل راستی‌آزمایی است.",
  },
  {
    icon: ShieldCheck,
    title: "حریم خصوصی واقعی",
    desc: "ایزولیشن ورک‌اسپیس در دو لایه اپ و دیتابیس؛ جستجوهای وب و مکان هم روی دستگاه خود کاربر اجرا می‌شوند، نه سرور ما.",
  },
  {
    icon: Workflow,
    title: "قابل تنظیم بدون کدنویسی",
    desc: "استودیوی ایجنت: دستورالعمل، ابزارها، مدل و دانش هر دستیار از پنل ادمین — تعریف دستیار جدید در چند دقیقه.",
  },
  {
    icon: DatabaseZap,
    title: "داده ساخت‌یافته",
    desc: "از لیدهای استخراجی تا جدول مشاغل O_NET و محاسبات اکسل؛ خروجی‌ها CSV/Excel می‌گیرند و وارد دانش می‌شوند.",
  },
];

const STACK = ["FastAPI", "PostgreSQL + pgvector", "Redis + Celery", "Next.js", "RAG هیبرید", "استریم SSE"];

export default function AboutPage() {
  return (
    <main className="min-h-screen">
      <SiteNav />
      <section className="mx-auto max-w-3xl px-4 pb-6 pt-10 text-center">
        <Badge variant="secondary">درباره ما</Badge>
        <h1 className="mt-2 text-3xl font-extrabold md:text-4xl">
          ما دستیار می‌سازیم، نه فقط چت‌بات
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-8 text-muted-foreground">
          AllMai یک پلتفرم مجموعه‌دستیارهاست: به‌جای یک چت‌بات عمومی، برای هر کار
          یک متخصص داری — معلم، مربی، یادداشت، استخراج داده و مشاور شغلی. همه
          فارسی، همه مستند، همه با حریم خصوصی کامل.
        </p>
      </section>
      <section className="mx-auto grid max-w-5xl gap-4 px-4 pb-6 md:grid-cols-2">
        {PILLARS.map((p) => (
          <div key={p.title} className="rounded-3xl border bg-white p-5 shadow-sm">
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10">
              <p.icon className="h-5 w-5 text-primary" />
            </span>
            <p className="mt-3 font-extrabold">{p.title}</p>
            <p className="mt-1 text-xs leading-6 text-muted-foreground">{p.desc}</p>
          </div>
        ))}
      </section>
      <section className="mx-auto max-w-3xl px-4 pb-6 text-center">
        <p className="text-xs font-bold text-muted-foreground">زیرساخت فنی</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {STACK.map((s) => (
            <span key={s} dir="ltr" className="rounded-full border bg-white px-3 py-1 font-mono text-xs">
              {s}
            </span>
          ))}
        </div>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button asChild size="lg">
            <Link href="/assistants">دیدن دستیارها</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/contact">تماس با ما</Link>
          </Button>
        </div>
      </section>
      <div className="pb-10" />
      <SiteFooter />
    </main>
  );
}
