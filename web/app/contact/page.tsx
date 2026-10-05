"use client";

import { useState } from "react";
import SiteNav from "@/components/SiteNav";
import SiteFooter from "@/components/SiteFooter";
import { Badge } from "@/components/ui/separator-badge";
import { Mail, Phone } from "lucide-react";

export default function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !body.trim()) {
      setError("نام و متن پیام لازم است.");
      return;
    }
    setError("");
    const mail = `mailto:hello@allmai.local?subject=${encodeURIComponent(
      `تماس از سایت — ${subject || "بدون موضوع"} (${name})`
    )}&body=${encodeURIComponent(`${body}\n\n— ${name}${email ? ` <${email}>` : ""}`)}`;
    window.location.href = mail;
  }

  return (
    <main className="min-h-screen">
      <SiteNav />
      <section className="mx-auto max-w-3xl px-4 pb-6 pt-10 text-center">
        <Badge variant="secondary">تماس با ما</Badge>
        <h1 className="mt-2 text-3xl font-extrabold md:text-4xl">حرف بزنیم</h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-7 text-muted-foreground">
          سؤال، پیشنهاد همکاری یا دستیار اختصاصی؟ پیام بده، زود جواب می‌دهیم.
        </p>
      </section>
      <section className="mx-auto grid max-w-3xl gap-4 px-4 pb-14 md:grid-cols-3">
        <div className="rounded-3xl border bg-white p-5 text-center shadow-sm">
          <Mail className="mx-auto h-6 w-6 text-primary" />
          <p className="mt-2 text-sm font-bold">ایمیل</p>
          <p dir="ltr" className="mt-1 font-mono text-xs text-muted-foreground">
            hello@allmai.local
          </p>
        </div>
        <div className="rounded-3xl border bg-white p-5 text-center shadow-sm">
          <Phone className="mx-auto h-6 w-6 text-primary" />
          <p className="mt-2 text-sm font-bold">تلفن</p>
          <p dir="ltr" className="mt-1 font-mono text-xs text-muted-foreground">
            +98-XXX-XXXXXXX
          </p>
        </div>
        <form
          onSubmit={submit}
          className="rounded-3xl border bg-white p-5 shadow-sm md:col-span-1"
        >
          <p className="text-sm font-bold">فرم پیام</p>
          <input
            className="mt-2 w-full rounded-xl border px-3 py-2 text-sm"
            placeholder="نام"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="mt-2 w-full rounded-xl border px-3 py-2 text-sm"
            dir="ltr"
            placeholder="ایمیل (اختیاری)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="mt-2 w-full rounded-xl border px-3 py-2 text-sm"
            placeholder="موضوع"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
          <textarea
            className="mt-2 w-full rounded-xl border px-3 py-2 text-sm"
            rows={4}
            placeholder="متن پیام…"
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
          <button
            type="submit"
            className="mt-2 w-full rounded-xl bg-primary py-2 text-sm text-white"
          >
            ارسال پیام
          </button>
          <p className="mt-1 text-[11px] text-slate-400">
            با دکمه ارسال، برنامه ایمیل شما باز می‌شود.
          </p>
        </form>
      </section>
      <SiteFooter />
    </main>
  );
}
