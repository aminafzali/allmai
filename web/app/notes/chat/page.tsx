"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowUp, Copy, FileAudio, History, Mic, Paperclip, Plus, RotateCcw, Square, StickyNote } from "lucide-react";
import { API_BASE, api, getToken, type StreamEvent } from "@/lib/admin";
import { streamChatResolvingTools } from "@/lib/clientToolLoop";
import { NOTES_WS_KEY, notesConvKey } from "@/lib/notes";
import HistoryDrawer, { type PastMessage } from "@/components/notes/HistoryDrawer";
import KbPicker from "@/components/notes/KbPicker";
import AddToDocs from "@/components/notes/AddToDocs";
import { FormError } from "@/components/Selectors";

type NoteItem = { id: string; text: string };
type Msg = {
  id: string;
  role: string;
  content: string;
  citations?: any[];
  kind?: "text" | "audio" | "note";
  audioUrl?: string;
  audioMime?: string;
  sourceId?: string | null;
  notes?: NoteItem[];
};
type Status = "idle" | "sending" | "searching" | "streaming" | "error" | "done";

const FALLBACK_SUGGESTIONS = [
  "خلاصه این منابع را بگو",
  "نکات مهم برای امتحان چیست؟",
  "یک سؤال تشریحی طرح کن",
];
function detectType(name: string): string {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (["doc", "docx"].includes(ext)) return "docx";
  if (["ppt", "pptx"].includes(ext)) return "pptx";
  if (ext === "txt") return "txt";
  if (ext === "md") return "md";
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) return "image";
  if (["mp3", "wav", "m4a", "ogg", "flac"].includes(ext)) return "audio";
  if (["mp4", "mov", "webm", "mkv"].includes(ext)) return "video";
  if (["xls", "xlsx"].includes(ext)) return "excel";
  if (ext === "csv") return "csv";
  return "pdf";
}
function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function NotesChatPage() {
  return (
    <Suspense fallback={null}>
      <NotesChatInner />
    </Suspense>
  );
}

function NotesChatInner() {
  const [ws, setWs] = useState("");
  const [kb, setKb] = useState("");
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [convTitle, setConvTitle] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Msg[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<string | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [uploading, setUploading] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK_SUGGESTIONS);
  const [recording, setRecording] = useState(false);
  const [recSecs, setRecSecs] = useState(0);
  const [plusMenu, setPlusMenu] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const searchParams = useSearchParams();
  const attachRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recMimeRef = useRef("");

  useEffect(() => {
    try {
      setWs(localStorage.getItem(NOTES_WS_KEY) ?? "");
    } catch {
      setWs("");
    }
    try {
      setConvId(localStorage.getItem(notesConvKey("")));
    } catch {
      setConvId(null);
    }
  }, []);

  // Locked KB scoping: each knowledge base resumes ONLY its own
  // conversation. Switching the picker never inherits another KB's
  // history — otherwise its answers leak into this KB.
  const kbRef = useRef<string | null>(null);
  useEffect(() => {
    if (kbRef.current === null) {
      kbRef.current = kb;
      return;
    }
    if (kbRef.current === kb) return;
    kbRef.current = kb;
    try {
      setConvId(localStorage.getItem(notesConvKey(kb)));
    } catch {
      setConvId(null);
    }
    setConvTitle("");
    setRemaining(0);
    setHistory([]);
    setStatus("idle");
    setError("");
    setLastFailed(null);
  }, [kb]);

  // menu "?history=1" opens the drawer even when already on this page
  // NOTE: searchParams object identity changes every render -> depend on
  // the stable string value only, otherwise Fast Refresh loops.
  const historyParam = searchParams.get("history");
  useEffect(() => {
    if (historyParam === "1") {
      setHistoryOpen(true);
      try {
        window.history.replaceState(null, "", window.location.pathname);
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyParam]);

  useEffect(() => {
    if (!ws) return;
    api(`/workspaces/${ws}/agents`).then(setAgents).catch(() => {});
  }, [ws]);

  // dynamic suggestions from this KB's real filenames (fallback: static)
  useEffect(() => {
    if (!ws || !kb) {
      setSuggestions(FALLBACK_SUGGESTIONS);
      return;
    }
    api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`)
      .then((items: any[]) => {
        const names = (items ?? [])
          .filter((s) => s?.status === "ready" && s?.filename)
          .slice(0, 3)
          .map((s) => String(s.filename));
        if (!names.length) {
          setSuggestions(FALLBACK_SUGGESTIONS);
          return;
        }
        const out = [`«${names[0]}» درباره چیست؟`, `نکات مهم «${names[0]}» را بگو`];
        if (names[1]) out.push(`فرق «${names[0]}» و «${names[1]}» چیست؟`);
        setSuggestions(out);
      })
      .catch(() => setSuggestions(FALLBACK_SUGGESTIONS));
  }, [ws, kb]);

  useEffect(() => {
    const a = agents.find((x) => x.key === "note_taking_assistant");
    if (a) setAgentId(a.id);
    else setAgentId("");
  }, [agents]);

  useEffect(() => {
    // smooth scroll on new messages, but instant during token streaming
    // to avoid queuing hundreds of smooth animations (jank / reload feel).
    try {
      bottomRef.current?.scrollIntoView({
        behavior: status === "streaming" ? "auto" : "smooth",
        block: "end",
      });
    } catch {
      /* ignore */
    }
  }, [history, status, uploading, recording]);

  useEffect(() => () => {
    if (recTimerRef.current) clearInterval(recTimerRef.current);
    recorderRef.current?.stream?.getTracks().forEach((t) => t.stop());
  }, []);

  function pushSystem(content: string) {
    setHistory((h) => [...h, { id: uid(), role: "system", content }]);
  }

  async function send(text: string, hiddenUser = false) {
    if (!text.trim() || !agentId) return;
    setError("");
    setLastFailed(null);
    setRemaining(0);
    setStatus("sending");
    if (!hiddenUser) setHistory((h) => [...h, { id: uid(), role: "user", content: text, kind: "text" }]);
    setMessage("");
    let acc = "";
    let cits: any[] = [];
    const streamId = uid();
    try {
      await streamChatResolvingTools(
        `/workspaces/${ws}/agents/${agentId}/chat/stream`,
        `/workspaces/${ws}/agents/${agentId}/chat/resume`,
        { message: text, kb_id: kb || null, conversation_id: convId },
        (e: StreamEvent) => {
          if (e.type === "meta") {
            setConvId(e.conversation_id);
            localStorage.setItem(notesConvKey(kb), e.conversation_id);
            cits = e.citations ?? [];
          } else if (e.type === "searching" || e.type === "progress") {
            setStatus("searching");
          } else if (e.type === "token") {
            setStatus("streaming");
            acc += e.text;
            const snapshot = acc;
            setHistory((h) => {
              const last = h[h.length - 1];
              if (last?.id === streamId) {
                return [...h.slice(0, -1), { ...last, content: snapshot }];
              }
              return [...h, { id: streamId, role: "assistant-stream", content: snapshot, kind: "text" }];
            });
          } else if (e.type === "done") {
            setConvId(e.conversation_id);
            localStorage.setItem(notesConvKey(kb), e.conversation_id);
            cits = e.citations ?? cits;
            if ((e as any).title) setConvTitle((e as any).title);
            setRemaining(e.remaining ?? 0);
            const final = acc;
            setHistory((h) => {
              const last = h[h.length - 1];
              const done: Msg = { id: uid(), role: "assistant", content: final, citations: cits, kind: "text" };
              return last?.id === streamId ? [...h.slice(0, -1), done] : [...h, done];
            });
            setStatus("done");
          } else if (e.type === "error") {
            throw new Error(e.message);
          }
        }
      );
    } catch (err: any) {
      setStatus("error");
      setError(err?.name === "AbortError" ? "قطع شد." : String(err?.message ?? err));
      if (!hiddenUser) setLastFailed(text);
      setHistory((h) => h.filter((m) => m.id !== streamId));
    }
  }

  async function waitReady(sourceId: string, name: string): Promise<any | null> {
    for (let i = 0; i < 45; i++) {
      await new Promise((r) => setTimeout(r, 4000));
      try {
        const list: any[] = await api(`/workspaces/${ws}/knowledge-bases/${kb}/sources`);
        const s = list.find((x) => x.id === sourceId);
        if (!s) return null;
        if (s.status === "ready") return s;
        if (s.status === "failed") {
          pushSystem(`تحلیل «${name}» ناموفق بود.`);
          return null;
        }
      } catch {
        return null;
      }
    }
    pushSystem(`تحلیل «${name}» طول کشید؛ متن کامل را از بخش فایل‌ها ببین.`);
    return null;
  }

  async function uploadFile(blob: Blob, filename: string, stype: string) {
    if (!ws || !kb) {
      setError("اول پایگاه دانش را انتخاب کن.");
      return;
    }
    setError("");
    setUploading(filename);
    pushSystem(`📤 «${filename}» ارسال شد؛ در حال تحلیل… (در بخش فایل‌ها هم ذخیره می‌شود)`);
    try {
      const form = new FormData();
      form.append("type", stype);
      form.append("file", blob, filename);
      const t = getToken();
      const r = await fetch(
        `${API_BASE}/workspaces/${ws}/knowledge-bases/${kb}/sources`,
        { method: "POST", headers: t ? { Authorization: `Bearer ${t}` } : {}, body: form }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const created = await r.json();
      const ready = await waitReady(created.id, filename);
      if (!ready) return;
      const tr: any = await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/sources/${created.id}/transcript`
      );
      const text: string = (tr?.text ?? "").trim();
      if (!text) {
        pushSystem(`«${filename}» آماده شد ولی متنی از آن استخراج نشد.`);
        return;
      }
      if (!agentId) {
        pushSystem(`📄 متن «${filename}»:\n\n${text.slice(0, 1500)}${text.length > 1500 ? "…" : ""}`);
        return;
      }
      await send(
        `متن فایل «${filename}» در ادامه آمده. یک خلاصه فارسی کوتاه (حداکثر ۸ خط) بده و در انتها بگو متن کامل در بخش فایل‌هاست:\n\n${text.slice(0, 12000)}`,
        true
      );
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setUploading("");
    }
  }

  /** Simple voice message (NOT live/streaming): the user records an audio
   *  file first, then it is uploaded and transcribed on the server. */
  async function uploadVoice(blob: Blob, filename: string, stype: string, localUrl: string, mime: string, msgId?: string) {
    if (!ws || !kb) {
      setError("اول پایگاه دانش را انتخاب کن.");
      return;
    }
    setError("");
    const id = msgId ?? uid();
    if (!msgId) {
      setHistory((h) => [...h, { id, role: "user", content: "🎙️ در حال استخراج متن صوت…", kind: "audio", audioUrl: localUrl, audioMime: mime }]);
    } else {
      setHistory((h) => h.map((m) => (m.id === id ? { ...m, audioUrl: localUrl, audioMime: mime, content: "🎙️ در حال استخراج متن صوت…" } : m)));
    }
    setUploading(filename);
    try {
      const form = new FormData();
      form.append("type", stype);
      form.append("file", blob, filename);
      const t = getToken();
      const r = await fetch(
        `${API_BASE}/workspaces/${ws}/knowledge-bases/${kb}/sources`,
        { method: "POST", headers: t ? { Authorization: `Bearer ${t}` } : {}, body: form }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const created = await r.json();
      const ready = await waitReady(created.id, filename);
      if (!ready) {
        setHistory((h) => h.map((m) => (m.id === id ? { ...m, content: "تحلیل صوت طول کشید؛ از بخش فایل‌ها دنبال کن.", sourceId: created.id } : m)));
        return;
      }
      const tr: any = await api(
        `/workspaces/${ws}/knowledge-bases/${kb}/sources/${created.id}/transcript`
      );
      const text: string = (tr?.text ?? "").trim();
      setHistory((h) =>
        h.map((m) =>
          m.id === id
            ? { ...m, content: text || "متنی از صوت استخراج نشد.", sourceId: created.id }
            : m
        )
      );
      // the voice message itself is the question: the chatbot answers it
      if (text && agentId) {
        await send(text.slice(0, 12000), true);
      }
    } catch (err: any) {
      setError(String(err?.message ?? err));
      setHistory((h) => h.map((m) => (m.id === id ? { ...m, content: "ارسال صوت ناموفق بود." } : m)));
    } finally {
      setUploading("");
    }
  }

  function onAttachPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) void uploadFile(f, f.name, detectType(f.name));
  }

  // ---- simple voice recording (NOT live): record first, transcribe after stop ----
  async function startSimpleRecord() {
    if (!ws || !kb) {
      setError("اول پایگاه دانش را انتخاب کن.");
      return;
    }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/webm", "audio/mp4", ""].find((m) => !m || MediaRecorder.isTypeSupported(m)) ?? "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recMimeRef.current = mime || "audio/webm";
      chunksRef.current = [];
      const msgId = uid();
      setHistory((h) => [...h, { id: msgId, role: "user", content: "🎙️ در حال ضبط… (دوباره بزن تا تمام شود)", kind: "audio" }]);
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (recTimerRef.current) clearInterval(recTimerRef.current);
        setRecording(false);
        const curMime = recMimeRef.current || "audio/webm";
        const ext = curMime.includes("mp4") ? "mp4" : curMime.includes("wav") ? "wav" : curMime.includes("mp3") ? "mp3" : "webm";
        const blob = new Blob(chunksRef.current, { type: curMime });
        const localUrl = URL.createObjectURL(blob);
        setRecSecs(0);
        if (blob.size) {
          // webm/mp4 -> video (audio-track transcription), mp3/wav/m4a -> audio
          const stype = ext === "webm" || ext === "mp4" ? "video" : "audio";
          const fname = ext === "webm" ? `voice-${Date.now()}.webm` : ext === "mp4" ? `voice-${Date.now()}.mp4` : `voice-${Date.now()}.${ext}`;
          void uploadVoice(blob, fname, stype, localUrl, curMime, msgId);
        } else {
          setHistory((h) => h.filter((m) => m.id !== msgId));
        }
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
      setRecSecs(0);
      recTimerRef.current = setInterval(() => setRecSecs((s) => s + 1), 1000);
    } catch {
      setError("دسترسی به میکروفون ممکن نیست.");
    }
  }

  function stopSimpleRecord() {
    try {
      recorderRef.current?.stop();
    } catch {
      setRecording(false);
    }
  }

  function onMicClick() {
    if (recording) {
      stopSimpleRecord();
      return;
    }
    setPlusMenu(false);
    void startSimpleRecord();
  }

  // ---- notes (no AI answer) ----
  function addNote() {
    const text = noteDraft.trim();
    if (!text) return;
    setError("");
    const nid = uid();
    // standalone note bubble (the chatbot never answers notes)
    setHistory((h) => [...h, { id: nid, role: "user", content: text, kind: "note" }]);
    setNoteDraft("");
    setNoteOpen(false);
    setPlusMenu(false);
  }

  async function copyMsg(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* clipboard unavailable */
    }
  }

  function newChat() {
    setConvId(null);
    setConvTitle("");
    setRemaining(0);
    setHistory([]);
    setStatus("idle");
    setError("");
    setLastFailed(null);
    try {
      localStorage.removeItem(notesConvKey(kb));
    } catch {
      /* ignore */
    }
  }

  function pickHistory(id: string, messages: PastMessage[], title: string) {
    setConvId(id);
    setConvTitle(title);
    try {
      localStorage.setItem(notesConvKey(kb), id);
    } catch {
      /* ignore */
    }
    setHistory(messages.map((m) => ({ id: uid(), role: m.role, content: m.content, citations: m.citations, kind: "text" as const })));
    setStatus("idle");
    setError("");
    setLastFailed(null);
    setHistoryOpen(false);
  }

  const busy = status === "sending" || status === "streaming" || status === "searching";
  const fmtSecs = `${Math.floor(recSecs / 60)}:${String(recSecs % 60).padStart(2, "0")}`;

  if (!ws) {
    return (
      <div className="rounded-3xl border bg-white p-6 text-center shadow-sm">
        <p className="text-sm font-extrabold">هنوز ورک‌اسپیسی انتخاب نشده</p>
        <Link href="/notes" className="mt-3 inline-block rounded-2xl bg-amber-600 px-5 py-2 text-sm font-bold text-white">
          انتخاب ورک‌اسپیس
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* KB picker + history */}
      <div className="flex flex-wrap items-center gap-2">
        <KbPicker ws={ws} value={kb} onChange={setKb} />
        <button
          onClick={() => setHistoryOpen(true)}
          className="flex items-center gap-1 rounded-2xl border bg-white px-3 py-2 text-xs shadow-sm hover:bg-slate-50"
        >
          <History className="h-4 w-4 text-amber-700" />
          گفتگوهای قبلی
        </button>
        {convId && (
          <button
            onClick={newChat}
            className="ms-auto rounded-full border bg-white px-3 py-1.5 text-xs hover:bg-slate-50"
          >
            گفتگوی جدید
          </button>
        )}
      </div>
      {convTitle && (
        <p className="mt-2 truncate text-xs font-bold text-slate-500">
          {convTitle}
        </p>
      )}
      <HistoryDrawer
        ws={ws}
        agentId={agentId}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onPick={pickHistory}
      />
      <FormError e={error} />
      {ws && !agentId && (
        <p className="mt-3 rounded-2xl border bg-white p-4 text-sm text-slate-500">
          دستیار یادداشت در این ورک‌اسپیس نیست — از{" "}
          <a className="text-amber-700 underline" href="/notes">صفحه یادداشت‌ها</a> بساز.
        </p>
      )}

      {/* messages */}
      <div className="mt-3 space-y-4 pb-44" aria-live="polite">
        {history.length === 0 && (
          <div className="pt-6 text-center">
            <p className="text-5xl">✎</p>
            <h2 className="mt-3 text-lg font-extrabold">از منابع خودت بپرس</h2>
            <p className="mx-auto mt-1 max-w-xs text-xs leading-5 text-slate-400">
              فایل، صوت کلاس یا لینک بفرست؛ دستیار فقط از روی همان‌ها جواب می‌دهد.
            </p>
            <div className="mx-auto mt-4 flex max-w-md flex-col gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  disabled={!agentId || busy}
                  className="rounded-2xl border bg-white px-4 py-2.5 text-sm shadow-sm transition hover:shadow disabled:opacity-40"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {history.map((m) =>
          m.role === "system" ? (
            <p key={m.id} className="mx-auto max-w-md rounded-full bg-slate-100 px-4 py-1.5 text-center text-[11px] leading-5 text-slate-500">
              {m.content}
            </p>
          ) : m.kind === "audio" ? (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[88%] rounded-3xl rounded-br-md bg-amber-600 px-4 py-3 text-sm leading-7 text-white shadow-sm">
                <p className="flex items-center gap-1.5 text-xs font-bold opacity-90">
                  <FileAudio className="h-4 w-4" /> پیام صوتی
                </p>
                {m.audioUrl && (
                  <audio controls src={m.audioUrl} className="mt-2 w-full" style={{ direction: "ltr" }} />
                )}
                <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6">{m.content}</p>
                <span className="mt-2 flex flex-wrap items-center gap-1 border-t border-white/20 pt-1.5">
                  <button
                    onClick={() => copyMsg(m.content)}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-white/80 hover:bg-white/10 hover:text-white"
                    title="کپی متن صوت"
                  >
                    <Copy className="h-3.5 w-3.5" /> کپی
                  </button>
                  <span className="text-white/60">·</span>
                  <AddToDocs ws={ws} kb={kb} text={m.content} sourceId={m.sourceId} tone="light" />
                </span>
                {m.sourceId && (
                  <p className="mt-1 text-[10px] text-white/70">✓ این صوت در اسناد و دانش (RAG) ذخیره شد.</p>
                )}
              </div>
            </div>
          ) : m.kind === "note" ? (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[85%] rounded-3xl rounded-br-md border border-yellow-200 bg-yellow-50 px-4 py-2.5 text-sm leading-7 text-slate-800 shadow-sm">
                <p className="flex items-center gap-1.5 text-xs font-bold text-yellow-800">
                  <StickyNote className="h-4 w-4" /> یادداشت
                </p>
                <p className="mt-1 whitespace-pre-wrap text-[13px] leading-6">{m.content}</p>
                <span className="mt-1.5 flex flex-wrap items-center gap-1 border-t border-yellow-200 pt-1.5">
                  <button
                    onClick={() => copyMsg(m.content)}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-yellow-700 hover:bg-yellow-100"
                    title="کپی"
                  >
                    <Copy className="h-3.5 w-3.5" /> کپی
                  </button>
                  <AddToDocs ws={ws} kb={kb} text={m.content} />
                </span>
              </div>
            </div>
          ) : m.role === "user" ? (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[85%] rounded-3xl rounded-br-md bg-amber-600 px-4 py-2.5 text-sm leading-7 text-white shadow-sm">
                <p className="whitespace-pre-wrap">{m.content}</p>
                <span className="mt-1.5 flex flex-wrap items-center gap-1 border-t border-white/20 pt-1.5">
                  <button
                    onClick={() => copyMsg(m.content)}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-white/80 hover:bg-white/10 hover:text-white"
                    title="کپی"
                  >
                    <Copy className="h-3.5 w-3.5" /> کپی
                  </button>
                  <span className="text-white/60">·</span>
                  <AddToDocs ws={ws} kb={kb} text={m.content} tone="light" />
                </span>
              </div>
            </div>
          ) : (
            <div key={m.id} className="flex gap-2">
              <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-sm">
                ✎
              </span>
              <div className="min-w-0 flex-1">
                <div className="rounded-3xl rounded-bl-md border bg-white px-4 py-3 text-sm leading-7 shadow-sm">
                  <p className="whitespace-pre-wrap">
                    {m.content}
                    {m.role === "assistant-stream" && <span className="animate-pulse"> ▍</span>}
                  </p>
                  {!!m.citations?.length && (
                    <div className="mt-2 flex flex-wrap gap-1" dir="ltr">
                      {m.citations.map((c: any) => (
                        <span key={c.n} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                          [{c.n}] {c.source ?? ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {m.role === "assistant" && (
                  <span className="mt-1 flex flex-wrap items-center gap-1 px-1">
                    <button
                      onClick={() => copyMsg(m.content)}
                      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                    >
                      <Copy className="h-3.5 w-3.5" /> کپی
                    </button>
                    <AddToDocs ws={ws} kb={kb} text={m.content} />
                  </span>
                )}
              </div>
            </div>
          )
        )}
        {status === "sending" && (
          <div className="flex gap-1 py-2" aria-label="در حال ارسال">
            {[0, 1, 2].map((d) => (
              <span key={d} className="h-2 w-2 animate-bounce rounded-full bg-amber-400" style={{ animationDelay: `${d * 150}ms` }} />
            ))}
          </div>
        )}
        {status === "searching" && <p className="text-xs text-slate-400">در حال جستجو…</p>}
        {status === "done" && remaining > 0 && (
          <button
            onClick={() => send("ادامه بده")}
            className="rounded-full bg-amber-600 px-4 py-1.5 text-xs font-bold text-white shadow hover:bg-amber-700"
          >
            ادامه ترجمه ({remaining} بخش مانده)
          </button>
        )}
        {uploading && !uploading.startsWith("voice-") && (
          <p className="text-xs text-slate-400">📤 «{uploading}» در حال ارسال و تحلیل…</p>
        )}
        {error && lastFailed && (
          <button
            onClick={() => send(lastFailed)}
            className="flex items-center gap-1 rounded-full border bg-white px-3 py-1.5 text-xs hover:bg-slate-50"
          >
            <RotateCcw className="h-3.5 w-3.5" /> تلاش مجدد
          </button>
        )}
        <div ref={bottomRef} />
      </div>

      {/* fixed input (ChatGPT-like: never moves) */}
      {agentId && (
        <div className="fixed inset-x-0 bottom-[72px] z-20 px-4 md:bottom-5">
          <div className="mx-auto w-full max-w-3xl">
            {recording && (
              <div className="mb-1 flex items-center justify-center gap-2 text-xs text-red-600">
                <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                در حال ضبط… {fmtSecs} (بزن تا تمام شود)
              </div>
            )}
            {noteOpen && (
              <div className="mb-1.5 rounded-3xl border border-yellow-200 bg-yellow-50/70 p-2 shadow-xl">
                <p className="flex items-center gap-1 px-1 text-[11px] font-bold text-yellow-800">
                  <StickyNote className="h-3.5 w-3.5" />
                  یادداشت جدید
                </p>
                <textarea
                  autoFocus
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  rows={2}
                  placeholder="یادداشتت را بنویس… (هوش مصنوعی به آن جواب نمی‌دهد)"
                  className="mt-1 max-h-28 w-full resize-none rounded-2xl border bg-white px-3 py-2 text-sm outline-none focus:border-yellow-400"
                />
                <div className="mt-1 flex gap-1.5">
                  <button
                    onClick={addNote}
                    disabled={!noteDraft.trim()}
                    className="flex-1 rounded-xl bg-yellow-500 py-1.5 text-xs font-bold text-white disabled:opacity-40 hover:bg-yellow-600"
                  >
                    ارسال یادداشت
                  </button>
                  <button
                    onClick={() => { setNoteOpen(false); setNoteDraft(""); }}
                    className="rounded-xl border bg-white px-4 py-1.5 text-xs hover:bg-slate-50"
                  >
                    انصراف
                  </button>
                </div>
              </div>
            )}
            <div className="relative flex items-end gap-1.5 rounded-3xl border bg-white p-2 shadow-xl">
              <button
                onClick={onMicClick}
                title="پیام صوتی"
                aria-label="پیام صوتی"
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition ${recording ? "bg-red-600 text-white" : "text-amber-700 hover:bg-amber-50"}`}
              >
                {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>
              <textarea
                className="max-h-32 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none"
                rows={1}
                placeholder="پیام…"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(message);
                  }
                }}
                disabled={busy}
              />
              <span className="relative shrink-0">
                <button
                  onClick={() => { setPlusMenu((v) => !v); }}
                  title="افزودن"
                  aria-label="افزودن"
                  className="flex h-9 w-9 items-center justify-center rounded-full text-amber-700 transition hover:bg-amber-50"
                >
                  <Plus className="h-5 w-5" />
                </button>
                {plusMenu && (
                  <>
                    <span className="fixed inset-0 z-10" onClick={() => setPlusMenu(false)} />
                    <span className="absolute bottom-11 right-0 z-20 block w-44 overflow-hidden rounded-2xl border bg-white shadow-xl">
                      <button
                        onClick={() => { setPlusMenu(false); attachRef.current?.click(); }}
                        className="flex w-full items-center gap-2 px-3 py-2.5 text-right text-xs hover:bg-amber-50"
                      >
                        <Paperclip className="h-4 w-4 text-amber-700" />
                        <span className="font-bold">الصاق فایل</span>
                      </button>
                      <button
                        onClick={() => { setPlusMenu(false); setNoteOpen(true); }}
                        className="flex w-full items-center gap-2 border-t px-3 py-2.5 text-right text-xs hover:bg-yellow-50"
                      >
                        <StickyNote className="h-4 w-4 text-yellow-600" />
                        <span className="font-bold">نوشتن یادداشت</span>
                      </button>
                    </span>
                  </>
                )}
              </span>
              <button
                type="button"
                aria-label="ارسال"
                onClick={() => send(message)}
                disabled={busy || !message.trim()}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-600 text-white transition disabled:opacity-40"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            </div>
            <input ref={attachRef} type="file" className="hidden" onChange={onAttachPicked} />
          </div>
        </div>
      )}
    </div>
  );
}
