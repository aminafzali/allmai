"""Baseline PII redaction for free-text that leaves our system boundary
(audit excerpts, summaries shown to other roles). Regex-based and
deliberately conservative: it over-redacts rather than leaks.

Not a substitute for access control — tenant scoping stays primary.
"""

import re

_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[card]"),
    (re.compile(r"\+?\d[\d\s\-()]{7,}\d"), "[phone]"),
    (re.compile(r"\b\d{10}\b"), "[id-number]"),  # e.g. national code shaped
]


def redact(text: str | None) -> str:
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
