"""Phase 3 Vision (D): triage + Gemini describe, eager in the worker.

Locked decisions (Spike, 12 calls):
- Triage: caption data-carrying (>= VISION_CAPTION_MIN_CHARS AND a digit)
  -> skip; figure page text-rich (>= VISION_PAGE_TEXT_MIN_CHARS,
  provisional) -> skip; else describe (cap DOC_VISION_MAX_FIGURES/source).
- Caption numbers are UNVERIFIED CLAIMS: the vision prompt instructs the
  model to report only image-verified values and never repeat caption
  numbers as fact (Spike V7 lesson: caption bias).
- Dense/small-label figures the model cannot read are a documented
  limitation (Spike V8): failures keep OUR placeholder (never model
  confabulation), flagged vision_failed/vision_degraded.
- Updates the EXISTING image chunk (content = caption + description,
  metadata vision_*); segments untouched; embedding runs AFTER vision.
"""

import re
from typing import Protocol

_DIGIT_RE = re.compile(r"\d")

VISION_DESCRIBE_PROMPT = (
    "این تصویر را دقیق توصیف کن: نوع نمودار یا شکل، عنوان، برچسب‌ها و "
    "همه اعداد و مقادیر."
    " قوانین سخت‌گیرانه:"
    " ۱) فقط چیزهایی را بنویس که مستقیماً در تصویر می‌بینی."
    " ۲) شرح همراه تصویر ممکن است نادرست باشد؛ اعداد آن را هرگز به‌عنوان واقعیت تکرار نکن"
    " مگر اینکه خودت همان عدد را در تصویر ببینی."
    " ۳) اگر عددی را واضح نمی‌بینی، بنویس «نامشخص» و چیزی حدس نزن."
)


def caption_is_data_carrying(caption: str, min_chars: int) -> bool:
    """A caption already carries data iff long enough AND has a digit."""
    caption = (caption or "").strip()
    return len(caption) >= min_chars and bool(_DIGIT_RE.search(caption))


def page_carries_data(page_text: str, min_chars: int) -> bool:
    """A rich page makes vision redundant only when it is BOTH long enough
    AND contains digits (validated: rich prose that merely references a
    chart holds no numbers — skipping vision there would lose the data)."""
    page_text = page_text or ""
    return len(page_text) >= min_chars and bool(_DIGIT_RE.search(page_text))


def triage_figures(drafts, page_texts: dict, max_figures: int,
                   caption_min_chars: int, page_text_min: int):
    """Split image drafts into (describe, skipped). Pure function.

    Returns (to_describe, skipped_reasons): skipped_reasons maps
    draft index -> reason ("caption-data" | "page-rich" | "over-cap").
    """
    images = [(i, d) for i, d in enumerate(drafts)
              if (d.metadata or {}).get("kind") in ("image", "figure")]
    to_describe: list[int] = []
    skipped: dict[int, str] = {}
    for i, d in images:
        caption = str((d.metadata or {}).get("caption", "") or "")
        if caption_is_data_carrying(caption, caption_min_chars):
            skipped[i] = "caption-data"
            continue
        if page_carries_data(page_texts.get(d.page_no) or "", page_text_min):
            skipped[i] = "page-rich"
            continue
        if len(to_describe) >= max_figures:
            skipped[i] = "over-cap"
            continue
        to_describe.append(i)
    return to_describe, skipped


class MultimodalProcessor(Protocol):
    def describe_figure(self, image_bytes: bytes, caption: str = "") -> str:
        """Return a figure description, or "" when unavailable."""
        ...


class NullMultimodalProcessor:
    """Stand-in: always skips (used when vision is disabled)."""

    provider_name = "null"

    def describe_figure(self, image_bytes: bytes, caption: str = "") -> str:
        return ""


class GeminiVisionProcessor:
    """Real MultimodalProcessor: gapgpt-compatible vision (no new provider)."""

    provider_name = "gemini-vision"

    def __init__(self, model: str | None = None, describe_fn=None) -> None:
        from app.ai.openai_compat import OpenAICompatProvider

        self._provider = OpenAICompatProvider()
        self._model = model
        self._describe_fn = describe_fn or self._provider.describe_image

    def describe_figure(self, image_bytes: bytes, caption: str = "") -> str:
        prompt = VISION_DESCRIBE_PROMPT
        if (caption or "").strip():
            prompt += (f" شرح همراه تصویر (تأییدنشده، فقط سرنخ): {caption.strip()[:300]}")
        try:
            text = (self._describe_fn(bytes(image_bytes or b""), prompt,
                                       model=self._model, max_tokens=800) or "")
        except Exception as exc:
            raise RuntimeError(
                f"Vision describe failed: {type(exc).__name__}: {exc}") from exc
        text = text.strip()
        if not text:
            raise RuntimeError("Vision describe returned empty text")
        return text
