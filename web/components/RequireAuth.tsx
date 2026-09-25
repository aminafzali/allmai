"use client";

import { useEffect, useState } from "react";
import { getToken } from "@/lib/admin";
import { usePathname, useRouter } from "next/navigation";

const PUBLIC_SUFFIXES = ["/login"];

export default function RequireAuth({
  children,
  to = "/admin/login",
}: {
  children: React.ReactNode;
  to?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [ok, setOk] = useState(false);
  const isPublic = PUBLIC_SUFFIXES.some((s) => pathname.endsWith(s));
  useEffect(() => {
    if (isPublic) {
      setOk(true);
      return;
    }
    if (!getToken()) router.replace(to);
    else setOk(true);
  }, [router, to, isPublic, pathname]);
  if (!ok) return <p className="p-8 text-sm text-muted-foreground">در حال بررسی ورود…</p>;
  return <>{children}</>;
}
