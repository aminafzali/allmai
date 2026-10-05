"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Bot,
  Cpu,
  Database,
  FileUp,
  FlaskConical,
  Gauge,
  Globe2,
  Home,
  LogOut,
  MessagesSquare,
  Settings2,
  Users,
  Warehouse,
} from "lucide-react";
import { clearToken } from "@/lib/admin";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator-badge";
import { Sheet, SheetContent, SheetHeader, SheetTrigger } from "@/components/ui/sheet";
import { Menu } from "lucide-react";

const GROUPS: { title: string; links: { href: string; label: string; icon: any }[] }[] = [
  {
    title: "نمای کلی",
    links: [{ href: "/admin", label: "داشبورد", icon: Gauge }],
  },
  {
    title: "سازماندهی",
    links: [
      { href: "/admin/users", label: "کاربران", icon: Users },
      { href: "/admin/workspaces", label: "ورک‌اسپیس‌ها", icon: Warehouse },
    ],
  },
  {
    title: "دانش",
    links: [
      { href: "/admin/knowledge-bases", label: "بیس‌های دانش", icon: Database },
      { href: "/admin/global-knowledge", label: "پایگاه‌های دانش سراسری", icon: Globe2 },
      { href: "/admin/sources", label: "سورس‌ها", icon: FileUp },
      { href: "/admin/documents", label: "اسناد", icon: FileUp },
      { href: "/admin/processing", label: "وضعیت پردازش", icon: Gauge },
      { href: "/admin/debug", label: "دیباگر retrieval", icon: FlaskConical },
    ],
  },
  {
    title: "هوش مصنوعی",
    links: [
      { href: "/admin/agent-studio", label: "استودیو ایجنت", icon: Bot },
      { href: "/admin/agents", label: "ایجنت‌ها", icon: Bot },
      { href: "/admin/conversations", label: "گفتگوها", icon: MessagesSquare },
      { href: "/admin/ai-settings", label: "تنظیمات مدل", icon: Settings2 },
    ],
  },
];

function SidebarBody({ current, onNav }: { current: string; onNav?: () => void }) {
  const router = useRouter();
  return (
    <div className="flex h-full flex-col">
      <Link href="/admin" onClick={onNav} className="flex items-center gap-2 px-1 py-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">
          ا
        </span>
        <span>
          <span className="block font-extrabold">AllMai Admin</span>
          <span className="block text-[11px] text-muted-foreground">پنل مدیریت پلتفرم</span>
        </span>
      </Link>
      <Separator className="my-3" />
      <nav className="flex-1 space-y-4 overflow-y-auto">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <p className="px-2 text-[11px] font-medium text-muted-foreground">{g.title}</p>
            <div className="mt-1 space-y-0.5">
              {g.links.map((l) => (
                <Link
                  key={l.href}
                  href={l.href}
                  onClick={onNav}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                    current === l.href || (l.href !== "/admin" && current.startsWith(l.href))
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                  )}
                >
                  <l.icon className="h-4 w-4 shrink-0" />
                  {l.label}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <Separator className="my-3" />
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="flex-1 justify-start text-muted-foreground"
          onClick={() => {
            clearToken();
            router.push("/admin/login");
          }}
        >
          <LogOut />
          خروج
        </Button>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <Home />
          </Link>
        </Button>
      </div>
      <p className="mt-2 flex items-center gap-1 px-1 text-[11px] text-muted-foreground">
        <Cpu className="h-3 w-3" />
        FastAPI + pgvector
      </p>
    </div>
  );
}

export default function AdminSidebar() {
  const pathname = usePathname();
  return (
    <>
      {/* دسکتاپ: سایدبار ثابت سمت راست (در dir=rtl اولین فلکس = راست) */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-l bg-card p-4 md:block">
        <SidebarBody current={pathname} />
      </aside>
      {/* موبایل: دکمه منو + کشو */}
      <div className="sticky top-0 z-40 flex items-center gap-2 border-b bg-background/90 px-4 py-2 backdrop-blur md:hidden">
        <Sheet>
          <SheetTrigger asChild>
            <Button variant="outline" size="icon" aria-label="منو">
              <Menu />
            </Button>
          </SheetTrigger>
          <SheetContent side="right">
            <SheetHeader>
              <span className="font-bold">منوی مدیریت</span>
            </SheetHeader>
            <SidebarBody current={pathname} />
          </SheetContent>
        </Sheet>
        <span className="font-bold">AllMai Admin</span>
      </div>
    </>
  );
}
