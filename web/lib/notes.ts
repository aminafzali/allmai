/** Shared keys for the notes app (hub/chat/files stay on one workspace). */
export const NOTES_WS_KEY = "allmai_notes_ws";
export const NOTES_CONV_KEY = "allmai_notes_conv";

/** One resumed conversation per knowledge base: switching the KB picker
 *  must NEVER resume (or inherit history from) another KB's conversation.
 *  `""` (the «همه» picker option) gets its own "all" slot. */
export function notesConvKey(kbId: string): string {
  return `${NOTES_CONV_KEY}:${kbId || "all"}`;
}

/** Drop every stored notes conversation (workspace switch). */
export function clearNotesConvs() {
  try {
    const doomed: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k === NOTES_CONV_KEY || k?.startsWith(`${NOTES_CONV_KEY}:`)) doomed.push(k);
    }
    doomed.forEach((k) => localStorage.removeItem(k));
  } catch {
    /* storage unavailable */
  }
}

export function getNotesWs(): string {
  try {
    return localStorage.getItem(NOTES_WS_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setNotesWs(id: string) {
  try {
    localStorage.setItem(NOTES_WS_KEY, id);
  } catch {
    /* storage unavailable */
  }
}
