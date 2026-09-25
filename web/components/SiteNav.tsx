"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GraduationCap, ShieldCheck } from "lucide-react";

export default function SiteNav() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">
            ا
          </span>
          <span className="font-extrabold tracking-tight">AllMai</span>
        </Link>
        <nav className="mr-6 hidden items-center gap-4 text-sm text-muted-foreground md:flex">
          <Link href="/#teacher" className="transition hover:text-foreground">
            معلم
          </Link>
          <Link href="/#student" className="transition hover:text-foreground">
            دانش‌آموز
          </Link>
          <Link href="/#features" className="transition hover:text-foreground">
            قابلیت‌ها
          </Link>
        </nav>
        {/* در چیدمان راست‌به‌چپ، ms-auto دکمه را به سمت چپ می‌راند */}
        <div className="ms-auto flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/teacher/login">
              <GraduationCap />
              ورود معلم
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/admin/login">
              <ShieldCheck />
              ادمین
            </Link>
          </Button>
        </div>
      </div>
    </header>
  );
}
