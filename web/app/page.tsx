import Link from "next/link";
import SiteNav from "@/components/SiteNav";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/separator-badge";
import { BookOpenCheck, BrainCircuit, MessagesSquare, SearchCheck, ShieldCheck, Sparkles } from "lucide-react";

const FEATURES = [
  { icon: BookOpenCheck, title: "طرح درس grounded", desc: "تولید طرح درس از منابع خودتان با ارجاع دقیق به صفحه و منبع." },
  { icon: BrainCircuit, title: "حافظه دانش‌آموز", desc: "مربی تحصیلی که اهداف و پیشرفت شما را به خاطر می‌سپارد." },
  { icon: SearchCheck, title: "بازیابی ترکیبی", desc: "جستجوی وکتوری + تمام‌متن + ریرنک با دیباگر شفاف." },
  { icon: MessagesSquare, title: "چت استریم", desc: "پاسخ لحظه‌ای توکن‌به‌توکن با نمایش منابع." },
  { icon: ShieldCheck, title: "ایزولیشن کامل", desc: "جداسازی داده‌ها بین ورک‌اسپیس‌ها در اپ و دیتابیس." },
  { icon: Sparkles, title: "مدل‌های ارزان", desc: "تنظیم مدل از پنل؛ ارائه‌دهنده‌های مختلف قابل تعویض." },
];

export default function Home() {
  return (
    <main className="min-h-screen">
      <SiteNav />

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-4 pb-10 pt-14 text-center">
        <Badge variant="secondary" className="mb-4">
          ✨ پلتفرم دستیارهای هوش مصنوعی آموزشی
        </Badge>
        <h1 className="mx-auto max-w-2xl text-3xl font-extrabold leading-10 md:text-4xl md:leading-[3rem]">
          دستیار هوشمندی که از <span className="text-primary">منابع خود شما</span> یاد می‌گیرد
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
          AllMai روی دانش اختصاصی شما (جزوه، کتاب، صوت کلاس) طرح درس حرفه‌ای می‌سازد و
          مسیر تحصیلی دانش‌آموز را قدم‌به‌قدم هدایت می‌کند.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button asChild size="lg">
            <Link href="/teacher/login">شروع به‌عنوان معلم</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/student/login">ورود دانش‌آموز</Link>
          </Button>
        </div>
      </section>

      {/* Role cards */}
      <section className="mx-auto grid max-w-5xl gap-4 px-4 md:grid-cols-2">
        <Card id="teacher" className="overflow-hidden">
          <div className="h-1.5 bg-gradient-to-l from-blue-600 to-sky-400" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <BookOpenCheck className="h-5 w-5 text-blue-600" />
              نسخه معلم
            </CardTitle>
            <CardDescription>
              کتاب و جزوه‌تان را بدهید، طرح درس آماده با اهداف، فعالیت، مثال، سؤال،
              ارزشیابی و ارجاع دقیق تحویل بگیرید.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button asChild>
              <Link href="/teacher/login">ورود معلم</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/teacher/lesson-plans">مشاهده طرح‌ها</Link>
            </Button>
          </CardContent>
        </Card>
        <Card id="student" className="overflow-hidden">
          <div className="h-1.5 bg-gradient-to-l from-green-600 to-emerald-400" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <BrainCircuit className="h-5 w-5 text-green-600" />
              نسخه دانش‌آموز
            </CardTitle>
            <CardDescription>
              مربی تحصیلی شما: برنامه هفتگی شخصی، پیگیری پیشرفت، یادآوری اهداف و
              پاسخ‌گویی بر اساس حافظه شما.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button asChild className="bg-green-700 hover:bg-green-800">
              <Link href="/student/login">ورود دانش‌آموز</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/student/plan">مشاهده برنامه</Link>
            </Button>
          </CardContent>
        </Card>
      </section>

      {/* Features */}
      <section id="features" className="mx-auto max-w-5xl px-4 py-12">
        <h2 className="text-center text-xl font-bold">چرا AllMai؟</h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <Card key={f.title}>
              <CardHeader className="pb-2">
                <f.icon className="h-6 w-6 text-primary" />
                <CardTitle className="text-base">{f.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs leading-5 text-muted-foreground">{f.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <footer className="border-t py-6 text-center text-xs text-muted-foreground">
        AllMai — FastAPI + PostgreSQL/pgvector + Next.js
      </footer>
    </main>
  );
}
