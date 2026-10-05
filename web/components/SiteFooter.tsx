import Link from "next/link";
import Logo from "@/components/Logo";
import { ASSISTANTS } from "@/lib/assistants";
import { AtSign, Clock, Globe, Mail, MapPin, Phone, Send } from "lucide-react";

export default function SiteFooter() {
  return (
    <footer className="relative overflow-hidden bg-blue-950 text-slate-200">
      <div className="h-1 bg-gradient-to-l from-orange-300 via-orange-200 to-orange-300" />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(500px 240px at 90% 0%, rgba(249,115,22,.10), transparent)",
        }}
      />
      <div className="relative mx-auto grid max-w-5xl gap-10 px-4 py-20 md:grid-cols-4 md:py-24">
        {/* vertical dividers: full height, top edge to bottom horizontal line */}
        <span aria-hidden="true" className="absolute bottom-0 top-0 hidden w-px bg-white/10 md:block" style={{ left: "75%" }} />
        <span aria-hidden="true" className="absolute bottom-0 top-0 hidden w-px bg-white/10 md:block" style={{ left: "50%" }} />
        <span aria-hidden="true" className="absolute bottom-0 top-0 hidden w-px bg-white/10 md:block" style={{ left: "25%" }} />
        <div>
          <p className="flex items-center gap-2.5">
            <Logo size={40} bare />
            <span className="text-xl font-extrabold tracking-wide text-white" dir="ltr">ALLMAI</span>
          </p>
          <p className="mt-3 max-w-xs text-xs leading-7 text-slate-300">
            مجموعه دستیارهای هوش مصنوعی فارسی؛ هر دستیار روی دانش اختصاصی
            شما جواب می‌دهد — با ارجاع دقیق، حافظه بلندمدت و جداسازی کامل
            داده‌ها. از آموزش تا استخراج لید و مشاوره شغلی.
          </p>
        </div>
        <div>
          <p className="font-extrabold text-white">دستیارها</p>
          <ul className="mt-3 space-y-2 text-sm text-slate-300">
            {ASSISTANTS.map((a) => (
              <li key={a.id}>
                <Link href={a.href} className="transition hover:text-orange-300">
                  {a.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="font-extrabold text-white">دسترسی سریع</p>
          <ul className="mt-3 space-y-2 text-sm text-slate-300">
            <li><Link href="/" className="transition hover:text-orange-300">خانه</Link></li>
            <li><Link href="/assistants" className="transition hover:text-orange-300">دستیارها</Link></li>
            <li><Link href="/about" className="transition hover:text-orange-300">درباره ما</Link></li>
            <li><Link href="/contact" className="transition hover:text-orange-300">تماس با ما</Link></li>
            <li><Link href="/admin/login" className="transition hover:text-orange-300">ورود ادمین</Link></li>
          </ul>
        </div>
        <div>
          <p className="font-extrabold text-white">ارتباط با ما</p>
          <ul className="mt-3 space-y-2.5 text-xs text-slate-300">
            <li className="flex items-center gap-2">
              <Mail className="h-3.5 w-3.5 shrink-0 text-orange-200/80" />
              <span dir="ltr" className="flex-1 text-right">hello@allmai.local</span>
            </li>
            <li className="flex items-center gap-2">
              <Phone className="h-3.5 w-3.5 shrink-0 text-orange-200/80" />
              <span dir="ltr" className="flex-1 text-right">+98-XXX-XXXXXXX</span>
            </li>
            <li className="flex items-center gap-2">
              <MapPin className="h-3.5 w-3.5 shrink-0 text-orange-200/80" />
              <span className="flex-1 text-right">تهران، خیابان تستی، پلاک ۱۲۳ (تستی)</span>
            </li>
            <li className="flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 shrink-0 text-orange-200/80" />
              <span className="flex-1 text-right">شنبه تا چهارشنبه، ۹ تا ۱۷ (تستی)</span>
            </li>
          </ul>
          <div className="mt-3 flex gap-2 text-orange-200/80">
            <a href="#" title="وب‌سایت (تستی)" className="flex h-8 w-8 items-center justify-center rounded-full border border-white/15 bg-white/5 transition hover:border-orange-300 hover:text-orange-300">
              <Globe className="h-4 w-4" />
            </a>
            <a href="#" title="اینستاگرام (تستی)" className="flex h-8 w-8 items-center justify-center rounded-full border border-white/15 bg-white/5 transition hover:border-orange-300 hover:text-orange-300">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-label="اینستاگرام">
                <rect width="20" height="20" x="2" y="2" rx="5" ry="5" />
                <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z" />
                <line x1="17.5" x2="17.51" y1="6.5" y2="6.5" />
              </svg>
            </a>
            <a href="#" title="شبکه اجتماعی (تستی)" className="flex h-8 w-8 items-center justify-center rounded-full border border-white/15 bg-white/5 transition hover:border-orange-300 hover:text-orange-300">
              <AtSign className="h-4 w-4" />
            </a>
            <a href="#" title="تلگرام (تستی)" className="flex h-8 w-8 items-center justify-center rounded-full border border-white/15 bg-white/5 transition hover:border-orange-300 hover:text-orange-300">
              <Send className="h-4 w-4" />
            </a>
          </div>
        </div>
      </div>
      <div className="relative border-t border-white/10">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-[11px] text-slate-400">
          <span>© 2026 AllMai — تمامی حقوق محفوظ است.</span>
          <span>ساخته‌شده با دقت برای کاربران فارسی‌زبان</span>
        </div>
      </div>
    </footer>
  );
}
