/** Orchestrates client-executed tool turns: stream -> tool_call(s) ->
 * browser execution -> POST /chat/resume -> tokens. At most MAX_ROUNDS
 * resume rounds (server caps too); then the stream ends normally.
 */
import { streamChat, type StreamEvent, type ToolCallEvent } from "./admin";
import { executeToolCall, type ToolResult } from "./browserTools";

const MAX_ROUNDS = 2;

export async function streamChatResolvingTools(
  streamPath: string,
  resumePath: string,
  body: unknown,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  let path = streamPath;
  let payload: unknown = body;
  for (let round = 0; round <= MAX_ROUNDS; round++) {
    const calls: ToolCallEvent[] = [];
    let convId: string | null = null;
    await streamChat(path, payload, (e) => {
      if (e.type === "tool_call") {
        calls.push(e);
        convId = e.conversation_id;
        return; // don't forward tool frames to the message list
      }
      if (e.type === "meta" && (e as any).conversation_id) {
        convId = (e as any).conversation_id;
      }
      onEvent(e);
    }, signal);
    if (!calls.length || !convId) return;
    if (round >= MAX_ROUNDS) return;
    onEvent({ type: "searching", conversation_id: convId, tool: calls[0]?.name });
    const results: ToolResult[] = [];
    for (const c of calls) {
      const conv = convId;
      results.push(await executeToolCall(c, signal, (msg) =>
        onEvent({ type: "progress", conversation_id: conv, stage: "browser", detail: msg })
      ));
    }
    path = resumePath;
    payload = { conversation_id: convId, results };
  }
}
