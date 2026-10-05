import {
  File,
  FileAudio,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Link2,
  StickyNote,
  type LucideIcon,
} from "lucide-react";

const MAP: Record<string, { icon: LucideIcon; bg: string; fg: string }> = {
  pdf: { icon: FileText, bg: "bg-red-100", fg: "text-red-600" },
  docx: { icon: FileText, bg: "bg-blue-100", fg: "text-blue-600" },
  pptx: { icon: FileText, bg: "bg-orange-100", fg: "text-orange-600" },
  txt: { icon: FileText, bg: "bg-slate-200", fg: "text-slate-600" },
  md: { icon: FileText, bg: "bg-slate-200", fg: "text-slate-600" },
  image: { icon: FileImage, bg: "bg-green-100", fg: "text-green-600" },
  audio: { icon: FileAudio, bg: "bg-purple-100", fg: "text-purple-600" },
  video: { icon: FileVideo, bg: "bg-rose-100", fg: "text-rose-600" },
  excel: { icon: FileSpreadsheet, bg: "bg-emerald-100", fg: "text-emerald-600" },
  csv: { icon: FileSpreadsheet, bg: "bg-emerald-100", fg: "text-emerald-600" },
  note: { icon: StickyNote, bg: "bg-amber-100", fg: "text-amber-600" },
  url: { icon: Link2, bg: "bg-sky-100", fg: "text-sky-600" },
};

export function fileVisual(type: string) {
  return MAP[type] ?? { icon: File, bg: "bg-slate-200", fg: "text-slate-500" };
}

export function StatusDot({ status, since }: { status: string; since?: string }) {
  if (status === "ready")
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
        <span className="h-2 w-2 rounded-full bg-green-500" /> آماده
      </span>
    );
  if (status === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-red-600">
        <span className="h-2 w-2 rounded-full bg-red-500" /> ناموفق
      </span>
    );
  const mins = since ? Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 60000)) : null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-amber-600">
      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
      در حال پردازش{mins !== null ? ` · ${mins} دقیقه` : ""}
    </span>
  );
}

export function formatBytes(n: number) {
  if (!n) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
