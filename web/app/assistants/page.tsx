import Link from "next/link";
import SiteNav from "@/components/SiteNav";
import SiteFooter from "@/components/SiteFooter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/separator-badge";
import { ASSISTANTS } from "@/lib/assistants";
import { ArrowLeft, Check } from "lucide-react";

export default function AssistantsPage() {
  return (
    <main className="min-h-screen">
      <SiteNav />
      <section className="mx-auto max-w-5xl px-4 pb-6 pt-10 text-center">
        <Badge variant="secondary">دستیارها</Badge>
        <h1 className="mt-2 text-3xl font-extrabold md:text-4xl">یک مجموعه، پنج متخصص</h1>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-7 text-muted-foreground">
          هر دستیار برای یک کار ساخته شده؛ ابزارها، مدل و دانش خودش را دارد و
          فقط از روی منابع متصل جواب می‌دهد.
        </p>
      </section>
      <section className="mx-auto grid max-w-5xl gap-4 px-4 pb-14 md:grid-cols-2">
        {ASSISTANTS.map((a) => (
          <Card key={a.id} className={`flex flex-col overflow-hidden shadow-sm backdrop-blur-xl transition hover:-translate-y-1 hover:shadow-xl ${a.card}`}>
            <CardHeader>
              <div className="flex items-start justify-between">
                <span className={`flex h-14 w-14 items-center justify-center rounded-2xl ${a.tile}`}>
                  <a.icon className={`h-7 w-7 ${a.iconColor}`} />
                </span>
                {a.badge && <Badge variant="secondary" className="border-orange-200 bg-orange-100 text-[11px] text-orange-800">{a.badge}</Badge>}
              </div>
              <CardTitle className="mt-3 text-xl">{a.title}</CardTitle>
              <p className="text-sm font-bold text-primary">{a.tagline}</p>
              <CardDescription className="text-sm leading-7">{a.desc}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col">
              <ul className="space-y-2 text-sm text-muted-foreground">
                {a.points.map((p) => (
                  <li key={p} className="flex items-start gap-2">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-blue-900" />
                    {p}
                  </li>
                ))}
              </ul>
              <div className="mt-5 flex gap-2 pt-2">
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
      </section>
      <SiteFooter />
    </main>
  );
}
