"""Note-taking assistant: answers from the user's files, notes and links.

Thin by design: chat/stream/conversations/KB assignment all run through
the generic AgentService + agents router (any agent key works once the
definition + instance exist). This package only owns the agent key, the
chat prompt default and the tools guide for excel answers.
"""

AGENT_KEY = "note_taking_assistant"

NOTE_CHAT_DEFAULT = (
    "You are a helpful study-notes assistant. Answer in Persian, concretely.\n"
    "Known about the user:\n{facts}\n"
    "Relevant memories:\n{memories}\n"
    "Reference material (files, notes, links):\n{context}\n"
    "History:\n{history}\n"
    "Student: {message}\nAssistant:"
)

NOTE_TOOLS_GUIDE = (
    "You have two knowledge paths: (1) retrieved context (files, notes, "
    "links — cite as [W1], [G1], ...); (2) structured Excel answers "
    "(cite as [X1], ...; the numbers are computed, quote them as-is). "
    "Informational questions use path 1; SUM/COUNT/AVG/GROUP BY and "
    "comparisons use path 2. Always reply in Persian."
)
