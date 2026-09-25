import "./globals.css";

export const metadata = { title: "AllMai Admin" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl">
      <body className="bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}
