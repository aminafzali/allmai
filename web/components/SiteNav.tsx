"use client";

import Link from "next/link";
import { useState } from "react";
import Logo from "@/components/Logo";
import { Home, Info, LayoutGrid, Menu, Phone, ShieldCheck, X } from "lucide-react";

const MENU = [
  { href: "/", label: "خانه", icon: Home },
  { href: "/assistants", label: "دستیارها", icon: LayoutGrid },
  { href: "/about", label: "درباره ما", icon: Info },
  { href: "/contact", label: "تماس با ما", icon: Phone },
];

export default function SiteNav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/70 shadow-[0_8px_30px_-12px_rgb(15_23_42/0.15)] backdrop-blur-xl">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center gap-2 px-4">
        <Link href="/" className="flex items-center gap-2" onClick={() => setOpen(false)}>
          <Logo size={34} />
          <span dir="ltr" className="text-lg font-bold tracking-widest text-blue-950">
            ALLMAI
          </span>
        </Link>
        {/* desktop menu */}
        <nav className="mr-6 hidden items-center gap-1 text-sm text-slate-500 md:flex">
          {MENU.map((m) => (
            <Link
              key={m.href}
              href={m.href}
              className="flex items-center gap-1.5 rounded-full px-3 py-1.5 transition hover:bg-slate-900/5 hover:text-blue-950"
            >
              <m.icon className="h-4 w-4" />
              {m.label}
            </Link>
          ))}
        </nav>
        {/* در چیدمان راست‌به‌چپ، ms-auto دکمه را به سمت چپ می‌راند */}
        <div className="ms-auto flex items-center gap-1">
          <Link
            href="/admin/login"
            className="hidden items-center gap-1.5 rounded-full px-3 py-1.5 text-sm text-slate-500 transition hover:bg-slate-900/5 hover:text-blue-950 md:flex"
          >
            <ShieldCheck className="h-4 w-4" />
            ادمین
          </Link>
          {/* mobile hamburger */}
          <button
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? "بستن منو" : "باز کردن منو"}
            aria-expanded={open}
            className="flex h-10 w-10 items-center justify-center rounded-full text-blue-950 transition hover:bg-slate-900/5 md:hidden"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>
      {/* mobile dropdown */}
      {open && (
        <nav className="border-t border-slate-200/60 bg-white/95 px-3 py-2 backdrop-blur-xl md:hidden">
          {MENU.map((m) => (
            <Link
              key={m.href}
              href={m.href}
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm text-slate-600 transition hover:bg-slate-900/5 hover:text-blue-950"
            >
              <m.icon className="h-4 w-4" />
              {m.label}
            </Link>
          ))}
          <Link
            href="/admin/login"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm text-slate-600 transition hover:bg-slate-900/5 hover:text-blue-950"
          >
            <ShieldCheck className="h-4 w-4" />
            ادمین
          </Link>
        </nav>
      )}
    </header>
  );
}
