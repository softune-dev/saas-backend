"""Art-director step: turns what a merchant typed into a real design brief
before it reaches the image model.

WHY THIS EXISTS: an A/B on the same hero banner showed the image model gives a
flat, generic result when it is handed a vague request like "make it nicer, add
a nice background and a beautiful button" verbatim — it has to guess what
"nicer" means, so it guesses the safest thing (a colour block and a pill). The
same model, given a concrete brief (layered background, lighting, palette, exact
type styles, button design), produced an editorial result. The brief is written
by a cheap text call (Flash, thinking off, ~700 tokens in / ~250 out, a fraction
of a cent) that can also SEE the current image, so "improve this" becomes a real
redesign that keeps the same model/product.

It never blocks a generation: any failure returns the original prompt unchanged
(CLAUDE.md rule 9's spirit — an optional enhancer must not fail the request).
"""

import base64
import logging

import httpx

from app import ai_log
from app.config import settings

log = logging.getLogger(__name__)

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM = """You are a senior art director for a premium e-commerce brand, briefing an AI image model.
Write ONE image-generation prompt (max 220 words, plain prose, no lists, no markdown) that makes the best possible image for the merchant's request.
You are given the merchant's request, the business, the aspect ratio, and often the current image.
Rules:
- If the merchant is unhappy ("ugly", "basic", "improve", "better", "make it beautiful") do NOT make a small tweak. Create a clearly more refined, professionally designed new version: keep the same model/product identity and brand feel, but redesign background, lighting, layout and any typography.
- Describe concretely: composition with a calm text-safe area, a rich layered background (soft gradient plus subtle texture or a styled studio/lifestyle set, depth, soft shadows, a few tasteful accents), cinematic soft lighting, a palette of 2-3 harmonious colours with one accent, premium editorial photography.
- Match the requested aspect ratio and keep every important element fully inside the frame.
- Never invent a logo. No watermarks.
{text_rules}
Return only the prompt."""

_WITH_TEXT = (
    "- Typography: name the exact words to render (short headline of 2-5 words, optional 5-8 word subline, "
    "a CTA button label of 1-2 words) unless the merchant gave their own wording, in which case use it "
    "character for character. Name the type style (e.g. elegant high-contrast serif headline, clean sans-serif "
    "subline), the hierarchy, and the button design (rounded pill, solid brand accent colour, high-contrast "
    "label, subtle shadow). All text crisp, correctly spelled and aligned."
)
_NO_TEXT = "- The image must contain NO text, letters, numbers, logos or buttons of any kind."


async def art_direct(
    request: str,
    *,
    context: str = "",
    aspect_ratio: str = "1:1",
    include_text: bool = True,
    reference: tuple[bytes, str] | None = None,
) -> str | None:
    """The rewritten prompt, or None when the step is off or failed (the caller
    then just uses the merchant's own prompt, as before)."""
    if not settings.ai_art_director_enabled or not settings.gemini_api_key or not request.strip():
        return None
    parts: list[dict] = []
    if reference:
        parts.append({"inlineData": {"mimeType": reference[1], "data": base64.b64encode(reference[0]).decode("ascii")}})
    note = f"{context}\n" if context else ""
    note += f"Aspect ratio: {aspect_ratio}.\nMerchant's request: {request.strip()}\n"
    if reference:
        note += "The attached image is the current image they want improved or changed."
    parts.append({"text": note})
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM.format(text_rules=_WITH_TEXT if include_text else _NO_TEXT)}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700, "thinkingConfig": {"thinkingBudget": 0}},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            res = await http.post(
                _URL.format(model=settings.ai_art_director_model),
                params={"key": settings.gemini_api_key},
                json=body,
            )
        ai_log.record_gemini(res)
        if res.status_code != 200:
            log.warning("art director failed: %s %s", res.status_code, res.text[:200])
            return None
        out = res.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in out).strip()
    except Exception:  # noqa: BLE001 — optional enhancer; any failure falls back.
        log.warning("art director errored", exc_info=True)
        return None
    return text or None
