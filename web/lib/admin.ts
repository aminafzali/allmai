export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

const ACCESS_KEY = "allmai_access";
const REFRESH_KEY = "allmai_refresh";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function setToken(t: string) {
  localStorage.setItem(ACCESS_KEY, t);
}

export function clearToken() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

let refreshFlight: Promise<string> | null = null;

/** Silent refresh using the stored refresh token (single-flight). */
export async function refreshAccess(): Promise<string> {
  if (refreshFlight) return refreshFlight;
  refreshFlight = (async () => {
    const rt = localStorage.getItem(REFRESH_KEY);
    if (!rt) throw new Error("unauthorized — please log in again");
    const r = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!r.ok) {
      clearToken();
      throw new Error("session expired — please log in again");
    }
    const data = await r.json();
    setToken(data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    return data.access_token as string;
  })();
  try {
    return await refreshFlight;
  } finally {
    refreshFlight = null;
  }
}

type ApiInit = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit | null };

async function doFetch(path: string, init: ApiInit | undefined, token: string | null) {
  const headers: Record<string, string> = { ...((init?.headers as object) ?? {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let body = init?.body ?? null;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers, body });
}

export async function api(path: string, init?: ApiInit): Promise<any> {
  let r = await doFetch(path, init, getToken());
  if (r.status === 401) {
    // Access token expired → silent refresh once, then retry the request.
    try {
      const fresh = await refreshAccess();
      r = await doFetch(path, init, fresh);
    } catch {
      clearToken();
      throw new Error("session expired — please log in again");
    }
  }
  if (r.status === 401) {
    clearToken();
    throw new Error("unauthorized — please log in again");
  }
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${r.status}: ${txt.slice(0, 300)}`);
  }
  const ct = r.headers.get("content-type") ?? "";
  return ct.includes("json") ? r.json() : r.text();
}

export async function login(email: string, password: string) {
  const r = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new Error("login failed — check email/password");
  const data = await r.json();
  setToken(data.access_token);
  localStorage.setItem(REFRESH_KEY, data.refresh_token);
  return api("/auth/me");
}

export async function apiHealth(): Promise<{ status: string }> {
  const r = await fetch(`${API_BASE}/health`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API unreachable: ${r.status}`);
  return r.json();
}

export type StreamEvent =
  | { type: "meta"; conversation_id: string; citations?: any[]; has_plan?: boolean }
  | { type: "token"; text: string }
  | { type: "tool_call"; conversation_id: string; call_id: string; name: string; arguments: { query: string }; client_spec?: any }
  | { type: "progress"; conversation_id: string; stage: string; detail: string }
  | { type: "searching"; conversation_id: string; tool?: string }
  | { type: "done"; conversation_id: string; citations?: any[]; has_plan?: boolean; leads_saved?: number; title?: string; remaining?: number }
  | { type: "error"; message: string };

export type ToolCallEvent = Extract<StreamEvent, { type: "tool_call" }>;

/** POST SSE stream against our own FastAPI. Calls onEvent per frame. */
export async function streamChat(
  path: string,
  body: unknown,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const open = (token: string | null) =>
    fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    });
  let r = await open(getToken());
  if (r.status === 401) {
    // Same silent refresh as api(), then open the stream once more.
    try {
      r = await open(await refreshAccess());
    } catch {
      clearToken();
      throw new Error("session expired — please log in again");
    }
  }
  if (r.status === 401) {
    clearToken();
    throw new Error("unauthorized — please log in again");
  }
  if (!r.ok || !r.body) {
    const txt = await r.text().catch(() => "");
    throw new Error(`${r.status}: ${txt.slice(0, 300)}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const emit = (line: string) => {
    const s = line.trim();
    if (!s.startsWith("data:")) return;
    const data = s.slice(5).trim();
    if (data === "[DONE]") return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      /* ignore malformed frame */
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) emit(line);
  }
  if (buf.trim()) emit(buf);
}
