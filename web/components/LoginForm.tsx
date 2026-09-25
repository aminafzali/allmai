"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, login } from "@/lib/admin";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginForm({
  title = "ورود به حساب",
  subtitle = "با ایمیل و رمز عبوری که مدیر داده وارد شوید.",
  redirectTo = "/admin",
}: {
  title?: string;
  subtitle?: string;
  redirectTo?: string;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState<any>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const user = await login(email, password);
      setMe(user);
      router.push(redirectTo);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  if (me || getToken()) {
    return (
      <Card className="shadow-sm">
        <CardContent className="pt-6 text-center">
          <p className="font-medium">وارد شده‌اید{me ? ` (${me.email})` : ""}.</p>
          <Button className="mt-3 w-full" onClick={() => router.push(redirectTo)}>
            ادامه
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="shadow-sm">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <div>
            <Label htmlFor="email">ایمیل</Label>
            <Input
              id="email"
              className="mt-1"
              placeholder="you@example.com"
              dir="ltr"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="password">رمز عبور</Label>
            <Input
              id="password"
              className="mt-1"
              placeholder="••••••••"
              type="password"
              dir="ltr"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={busy || !email || !password}>
            {busy ? "در حال ورود…" : "ورود"}
          </Button>
          {error && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
