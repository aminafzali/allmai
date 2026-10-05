import Link from "next/link";
import SiteNav from "@/components/SiteNav";
import SiteFooter from "@/components/SiteFooter";
import Logo from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/separator-badge";
import { ASSISTANTS } from "@/lib/assistants";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  DatabaseZap,
  MessagesSquare,
  ShieldCheck,
  Sparkles,
  Workflow,
  BrainCircuit,
} from "lucide-react";

const WHY = [
  {
    icon: MessagesSquare,
    title: "جواب grounded با ارجاع",
    desc: "هر دستیار فقط از روی منابع متصل جواب می‌دهد و جمله‌به‌جمله ارجاع [W] و [G] می‌چسباند؛ توهم حداقلی.",
  },
  {
    icon: BrainCircuit,
    title: "حافظه و پروفایل",
    desc: "اهداف، تیپ شخصیتی و حقایق شما ذخیره می‌ماند تا گفتگوهای بعدی از همان‌جا ادامه پیدا کند.",
  },
  {
    icon: ShieldCheck,
    title: "ایزولیشن کامل داده",
    desc: "جداسازی ورک‌اسپیس در لایه اپ و دیتابیس (RLS)؛ داده یک مجموعه هرگز به مجموعه دیگر نشت نمی‌کند.",
  },
  {
    icon: Sparkles,
    title: "استریم زنده",
    desc: "پاسخ توکن‌به‌توکن با نمایش منابع؛ سریع، شفاف و قابل قطع.",
  },
  {
    icon: Workflow,
    title: "مدل‌های قابل تعویض",
    desc: "پرووایدر و مدل هر دستیار از پنل ادمین عوض می‌شود؛ بدون تغییر کد، بدون قفل فروشنده.",
  },
  {
    icon: DatabaseZap,
    title: "جستجو روی دستگاه شما",
    desc: "ابزارهای وب و مکان در مرورگر خودتان اجرا می‌شوند؛ هیچ جستجویی از سرور ما انجام نمی‌شود.",
  },
];

const STEPS = [
  { n: "۱", title: "دستیار را انتخاب کن", desc: "معلم، مربی، یادداشت، استخراج داده یا مشاور هدایت تحصیلی — هر کدام برای یک کار ساخته شده." },
  { n: "۲", title: "دانشت را وصل کن", desc: "فایل، جزوه، صوت، لینک یا پایگاه سراسری؛ دستیار فقط از همان‌ها یاد می‌گیرد." },
  { n: "۳", title: "گفتگو کن و خروجی بگیر", desc: "طرح درس، برنامه تحصیلی، جدول لید یا نقشه راه شغلی — با ارجاع دقیق." },
];

/** Section header: right-aligned, Yekan, big orange label + short
 *  orange underline + title. Max two elements (label + title). */
function SectionHead({ label, title }: { label: string; title: string }) {
  return (
    <div className="text-right">
      <p className="text-base font-extrabold text-orange-600">{label}</p>
      <span aria-hidden="true" className="ml-auto mt-2 block h-1 w-16 rounded-full bg-orange-500" />
      <h2 className="mt-3 text-2xl font-extrabold text-blue-950 md:text-3xl">{title}</h2>
    </div>
  );
}

const FAQ = [  {
    q: "این دستیارها با چت‌بات‌های عمومی چه فرقی دارند؟",
    a: "چت‌بات عمومی از حافظه خودش جواب می‌دهد؛ دستیارهای ما به پایگاه دانش شما وصل‌اند و هر ادعا را با ارجاع به منبع (فایل، صفحه، دقیقه صوت) ثابت می‌کنند.",
  },
  {
    q: "داده‌های من کجا ذخیره می‌شود و چه کسی می‌بیند؟",
    a: "داده‌ها در دیتابیس اختصاصی همین پلتفرم می‌مانند و با ایزولیشن ورک‌اسپیس (سطح اپ + دیتابیس) فقط اعضای همان مجموعه به آن دسترسی دارند.",
  },
  {
    q: "جستجوی اینترنتی واقعاً از سرور شما انجام نمی‌شود؟",
    a: "درست است. ابزارهای وب و مکان در مرورگر خود شما (گوشی یا لپ‌تاپ) اجرا می‌شوند؛ سرور فقط نتیجه را اعتبارسنجی و ذخیره می‌کند.",
  },
  {
    q: "می‌توانم دستیار اختصاصی خودم را بسازم؟",
    a: "بله — از پنل ادمین (استودیوی ایجنت) دستورالعمل، ابزارها، مدل و دانش سراسری هر دستیار را می‌شود جداگانه تنظیم کرد.",
  },
  {
    q: "برای شروع چه چیزی لازم است؟",
    a: "فقط یک ورک‌اسپیس و چند فایل یا لینک. دستیار هر مجموعه با یک کلیک ساخته می‌شود و گفتگو بلافاصله شروع می‌شود.",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen">
      <SiteNav />

      {/* Hero */}
      <section className="relative overflow-hidden bg-blue-950 text-white">
        <div className="bg-grid-faint-light pointer-events-none absolute inset-0" />
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(600px 300px at 85% 10%, rgba(249,115,22,.16), transparent), radial-gradient(500px 260px at 10% 90%, rgba(249,115,22,.08), transparent)",
          }}
        />
        <div className="relative mx-auto max-w-5xl px-4 pb-16 pt-16 text-center md:pb-20 md:pt-20">
          <div className="mb-6 flex items-center justify-center gap-5">
            <Logo size={120} bare />
            <h1 className="text-right text-3xl font-extrabold leading-snug md:text-5xl md:leading-[3.5rem]">
              آلمای، هر کاری
              <br />
              یک دستیار هوشمند
            </h1>
          </div>
          <div className="mx-auto max-w-xl text-sm leading-8 text-slate-300 md:text-base">
            <p>از آموزش و پژوهش تا برنامه‌ریزی و مدیریت کارها؛ با مجموعه‌ای از دستیارهای تخصصی و ابزارهای هوش مصنوعی، متناسب با نیاز شما.</p>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Button asChild size="lg" className="bg-white text-slate-900 hover:bg-slate-200">
              <Link href="/assistants">مشاهده دستیارها</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="border-white/30 bg-transparent text-white hover:bg-white/10 hover:text-white">
              <Link href="/about">درباره ما</Link>
            </Button>
          </div>
          <div className="mx-auto mt-10 grid max-w-lg grid-cols-3 gap-2 text-center">
            {[
              { n: "۵", t: "دستیار تخصصی" },
              { n: "۱۰۰٪", t: "پاسخ مستند" },
              { n: "۰", t: "نشت داده" },
            ].map((s) => (
              <div key={s.t} className="rounded-2xl border border-white/15 bg-white/10 px-2 py-3 backdrop-blur">
                <p className="text-2xl font-extrabold text-orange-200">{s.n}</p>
                <p className="mt-1 text-[11px] text-slate-200">{s.t}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Assistants */}
      <section id="assistants" className="scroll-mt-20 bg-gradient-to-b from-slate-100/80 via-slate-50 to-white">
        <div className="mx-auto max-w-5xl px-4 py-14">
        <SectionHead label="دستیارها" title="یکی را انتخاب کن، کارت را جلو ببر" />
        <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {ASSISTANTS.map((a) => (
            <Card key={a.id} className={`group flex flex-col overflow-hidden shadow-sm backdrop-blur-xl transition hover:-translate-y-1 hover:shadow-xl ${a.card}`}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <span className={`flex h-12 w-12 items-center justify-center rounded-2xl ${a.tile}`}>
                    <a.icon className={`h-6 w-6 ${a.iconColor}`} />
                  </span>
                  {a.badge && (
                    <Badge variant="secondary" className="border-orange-200 bg-orange-100 text-[11px] text-orange-800">{a.badge}</Badge>
                  )}
                </div>
                <CardTitle className="mt-3 text-lg">{a.title}</CardTitle>
                <p className="text-xs font-bold text-primary">{a.tagline}</p>
                <CardDescription className="text-xs leading-6">{a.desc}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col">
                <ul className="space-y-1.5 text-xs text-muted-foreground">
                  {a.points.map((p) => (
                    <li key={p} className="flex items-start gap-1.5">
                      <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-900" />
                      {p}
                    </li>
                  ))}
                </ul>
                <div className="mt-4 flex gap-2 pt-2">
                  <Button asChild className={a.button}>
                    <Link href={a.href}>ورود <ArrowLeft className="h-4 w-4" /></Link>
                  </Button>
                  <Button asChild variant="outline">
                    <Link href={a.chatHref}>گفتگو</Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
          {/* CTA card */}
          <Card className="flex flex-col justify-center overflow-hidden border-orange-500/30 bg-gradient-to-bl from-blue-950 to-slate-900 text-white shadow-lg">
            <CardHeader>
              <CardTitle className="text-lg">دستیار ششم را تو بساز</CardTitle>
              <CardDescription className="text-xs leading-6 text-slate-300">
                از استودیوی ایجنت، دستورالعمل و ابزار خودت را تعریف کن.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button asChild className="bg-orange-200 text-blue-950 hover:bg-orange-300">
                <Link href="/admin/login">استودیوی ایجنت</Link>
              </Button>
            </CardContent>
          </Card>
        </div>
        </div>
      </section>

      {/* Why */}
      <section className="border-y border-slate-200/70 bg-white/80 backdrop-blur">
        <div className="mx-auto max-w-5xl px-4 py-14">
          <SectionHead label="چرا AllMai؟" title="ساخته‌شده برای اعتماد" />
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {WHY.map((f) => (
              <Card key={f.title} className="border-white/70 bg-slate-100/70 shadow-sm backdrop-blur-xl transition hover:shadow-lg">
                <CardHeader className="pb-2">
                  <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white shadow-sm">
                    <f.icon className="h-5 w-5 text-blue-950" />
                  </span>
                  <CardTitle className="mt-2 text-base">{f.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs leading-6 text-muted-foreground">{f.desc}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Steps */}
      <section className="bg-gradient-to-b from-white via-slate-50 to-white">
        <div className="mx-auto max-w-5xl px-4 py-14">
        <SectionHead label="شروع در سه قدم" title="پنج دقیقه تا اولین جواب مستند" />
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {STEPS.map((s, i) => (
            <div key={s.n} className="relative rounded-3xl border border-white/70 bg-slate-100/70 p-5 text-center shadow-sm backdrop-blur-xl">
              <span className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-blue-950 text-lg font-extrabold text-white">
                {s.n}
              </span>
              <p className="mt-3 font-extrabold">{s.title}</p>
              <p className="mt-1 text-xs leading-6 text-muted-foreground">{s.desc}</p>
              {i < STEPS.length - 1 && (
                <span className="absolute left-[-14px] top-1/2 hidden -translate-y-1/2 text-slate-300 md:block">◀</span>
              )}
            </div>
          ))}
        </div>
        </div>
      </section>

      {/* FAQ — formal */}
      <section className="border-t border-slate-200/70 bg-white">
        <div className="mx-auto max-w-5xl px-4 py-14">
          <SectionHead label="سوالات متداول" title="هر آنچه باید بدانی" />
          <div className="mt-8 overflow-hidden rounded-xl border border-slate-200 bg-slate-100/70 shadow-sm backdrop-blur-xl">
            {FAQ.map((f, i) => (
              <details
                key={f.q}
                open={i === 0}
                className={`group bg-white/60 px-5 py-5 transition hover:bg-white ${i > 0 ? "border-t border-slate-200" : ""}`}
              >
                <summary className="flex cursor-pointer list-none items-center gap-3 [&::-webkit-details-marker]:hidden">
                  <span className="font-mono text-xs font-bold text-blue-900" dir="ltr">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="flex-1 text-sm font-bold text-slate-800">{f.q}</span>
                  <ChevronDown className="h-4 w-4 shrink-0 text-blue-900 transition-transform duration-200 group-open:rotate-180" />
                </summary>
                <p className="mt-2 border-r-2 border-blue-900/70 pr-3 text-xs leading-7 text-muted-foreground">
                  {f.a}
                </p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="bg-gradient-to-b from-white to-slate-100/80">
        <div className="mx-auto max-w-5xl px-4 pb-16 pt-4">
        <div className="relative overflow-hidden rounded-2xl bg-blue-950 px-6 py-14 text-center text-white shadow-md md:py-16">
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(420px 220px at 80% 15%, rgba(249,115,22,.18), transparent)",
            }}
          />
          <p className="relative text-xs font-bold text-orange-200">شروع رایگان</p>
          <h2 className="relative mt-2 text-2xl font-extrabold md:text-3xl">
            آماده‌ای از دستیار هوشمندت استفاده کنی؟
          </h2>
          <p className="relative mx-auto mt-3 max-w-md text-sm leading-7 text-slate-300">
            وارد شو، دستیار موردنظرت را انتخاب کن و با کمک دانش و ابزارهای هوشمند، کارت را شروع کن.
          </p>
          <div className="relative mt-9 flex flex-wrap justify-center gap-3">
            <Button asChild size="lg" className="bg-orange-200 px-8 text-blue-950 shadow-lg hover:bg-orange-300">
              <Link href="/assistants">شروع با دستیارها</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="border-white/30 bg-white/5 px-8 text-white backdrop-blur hover:bg-white/15 hover:text-white">
              <Link href="/contact">تماس با ما</Link>
            </Button>
          </div>
        </div>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
