"""Server-sent events for streaming chat (our own FastAPI -> Next.js).

Frame format: `data: {json}\\n\\n`, terminated by `data: [DONE]`.
Event types: meta (conversation_id + citations) -> token* -> done | error.
Client-tool turns: meta -> tool_call+ (browser executes, POSTs /chat/resume)
-> meta -> token* -> done. At most one external tool per turn.
"""

import json

from fastapi.responses import StreamingResponse


async def event_stream(agen):
    async for event in agen:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def sse_response(agen) -> StreamingResponse:
    return StreamingResponse(event_stream(agen), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
