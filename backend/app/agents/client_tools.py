"""Client-executed search tools (browser runs, server never searches).

How it works: when an agent definition enables ``web_search``/``maps_search``,
the chat pipeline asks the chat LLM (one extra function-calling round —
this DOES cost tokens/API, capped at MAX_CLIENT_ROUNDS per turn) whether
the question needs one of them. If yes, the API streams a ``tool_call``
SSE event instead of tokens; the Next.js app executes the provider in the
USER'S BROWSER (Overpass/DDG specs from ai/*_providers) and POSTs results
to ``/chat/resume``. The server validates, stores leads, and streams the
final answer. At most one external tool runs per turn (never both).

Server-side provider impls (Gemini grounding, Google Places) stay dormant
unless explicitly selected + keyed in config.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MAX_CLIENT_ROUNDS = 2
MAX_RESULTS_PER_CALL = 30

WEB_SEARCH_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public internet for current/external information "
            "(news, prices, weather, fresh facts). Use ONLY when the answer "
            "needs up-to-date or out-of-knowledge facts. "
            "runs in the user's browser."),
        "parameters": {
            "type": "object",
            "properties": {"query": {
                "type": "string",
                "description": "standalone web query in the user language"}},
            "required": ["query"],
        },
    },
}

MAPS_SEARCH_SPEC = {
    "type": "function",
    "function": {
        "name": "maps_search",
        "description": (
            "Find places/businesses (name, address, phone, hours, location) "
            "for location questions: addresses, nearby restaurants/hotels, "
            "opening hours, directions. Use ONLY for place questions. "
            "runs in the user's browser."),
        "parameters": {
            "type": "object",
            "properties": {"query": {
                "type": "string",
                "description": "place query, e.g. 'رستوران ایتالیایی تهران'"}},
            "required": ["query"],
        },
    },
}

CLIENT_TOOL_SPECS: dict[str, dict] = {
    "web_search": WEB_SEARCH_SPEC,
    "maps_search": MAPS_SEARCH_SPEC,
}


def specs_for(tools: list[str]) -> list[dict]:
    """Function specs for the enabled client tools (order-stable)."""
    return [CLIENT_TOOL_SPECS[t] for t in tools if t in CLIENT_TOOL_SPECS]


def validate_call(name: str, arguments: object) -> dict | None:
    """Clean one LLM-emitted call. None when malformed/unknown."""
    if name not in CLIENT_TOOL_SPECS or not isinstance(arguments, dict):
        return None
    query = str(arguments.get("query") or "")[:500].strip()
    if not query:
        return None
    return {"name": name, "arguments": {"query": query}}


def request_client_calls(message: str, history: list, tools: list[str],
                         model: str | None = None) -> list[dict]:
    """One LLM routing round: returns 0..1 validated client tool calls.

    COST NOTE (honest): this is an extra chat-completions round on the
    admin's dime (GapGPT/OpenAI-compatible). It fires only when the
    definition enables web_search/maps_search. Any failure -> [] (the
    turn continues with local knowledge only).
    """
    specs = specs_for(tools)
    if not specs:
        return []
    try:
        from app.ai.openai_compat import OpenAICompatProvider

        msgs = []
        for m in (history or [])[-6:]:
            role = m.get("role") if isinstance(m, dict) else None
            text = m.get("content") if isinstance(m, dict) else None
            if role in ("user", "assistant") and text:
                msgs.append({"role": role, "content": str(text)[:800]})
        msgs.append({"role": "user", "content": message})
        res = OpenAICompatProvider().generate_with_tools(
            msgs, specs, model=model, temperature=0.0, max_tokens=300)
    except Exception as exc:
        log.warning("client tool routing failed: %s", exc)
        return []
    out = []
    for call in res.get("calls") or []:
        clean = validate_call(call.get("name"), call.get("arguments"))
        if clean is not None:
            out.append(clean)
        if len(out) >= 1:  # at most ONE external tool per turn
            break
    return out
