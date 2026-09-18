"""Preset prompts for AI image generation — the "/underwater"-style picker
the merchant browses instead of writing a prompt from scratch.

Deliberately a plain Python list, not a database table: these are curated
content the team writes (often with a different LLM's help, per how this
was scoped), not user data, so they ship with a code deploy like the theme
catalog does, not through an admin CRUD screen.

Each preset's `thumbnail` is a path served from the dashboard's own
`public/ai-presets/` folder — drop a PNG/WEBP there with the matching
filename and it shows up; nothing here reads image bytes, this is just the
prompt + metadata half of the pair. `prompt` uses `{subject}` as the one
substitution point for whatever the merchant is actually generating for
(a product name, a store name, a category name) — see
app/api/ai_images.py's `resolve_prompt`.

Categories mirror the site-editor sections a generated image is destined
for (hero/category/product) plus three standalone-graphic buckets:
`bento_showcase` (a multi-cell product-angle grid banner), `events` (a
promo/announcement graphic), and `marketing` (social posts/ad creative).
Ten presets per category is the target scope; each category below ships
with a real, working starter set — extend freely, this file is the only
place that needs an edit.

TEXT MODEL — every preset now supports text as a MERCHANT CHOICE, not a
fixed per-category rule. `prompt` is always the base scene/photography
description and never mentions text, headlines, or buttons — it must
stand on its own as a complete, good-looking image with nothing baked in.
`text_addon` is a separate sentence appended ONLY when the merchant opts
into text (see app/api/ai_images.py's resolve_prompt): what copy to add
and roughly where, written so it reads naturally appended after `prompt`.
Every preset here has a `text_addon` — even `category`, where a merchant
might still want a category name baked into the tile. When text is
requested, TEXT_RENDER_QUALITY (below) is appended after the addon on
every single call — that's the one place text-rendering instructions
live, so improving Gemini's output is a one-line edit, not a 40-preset
find-and-replace. When text is NOT requested, NO_TEXT_INSTRUCTION is
appended instead, so the model is never left to guess either way.
"""

# Appended after a preset's `prompt` + `text_addon` whenever the merchant
# has opted into baked-in text. This is the actual answer to "Gemini's text
# looks basic and dull next to ChatGPT's" — a generic image model left to
# guess at typography usually picks a mediocre font, drifts off the exact
# wording, or garbles a letter here and there. Spelling out the same
# professional-designer constraints a human would apply (one typeface,
# short copy, real contrast, a final proofread pass) is what actually moves
# the needle, far more than any scene description ever could. Edit THIS
# constant to improve every preset's text quality at once.
TEXT_RENDER_QUALITY = (
    "The text must look like it was placed by a professional graphic "
    "designer, not guessed at by an AI: choose ONE clean modern sans-serif "
    "typeface and use it consistently for every word, render each "
    "letterform fully formed with correct proportions — no distortion, "
    "warping, doubled strokes, melted edges, missing letters, or invented "
    "characters — and keep spacing and baseline alignment consistent "
    "across every line. Place the text over a flat or softly blurred area "
    "with genuine contrast against what's behind it; add a subtle dark "
    "scrim behind light text or a soft light scrim behind dark text if the "
    "background underneath is busy, so it reads instantly at a glance. "
    "Keep any headline under 5 words and any button label under 3 words — "
    "short copy renders far more reliably than long copy, and a model that "
    "runs out of room is what causes broken or overlapping letters. Before "
    "finishing, re-check every single word against exactly what was "
    "specified, letter by letter — correct spelling, no repeated letters, "
    "no dropped letters, nothing invented."
)

# Appended instead of TEXT_RENDER_QUALITY when the merchant did NOT opt
# into text — an image-generation model told nothing about text will
# sometimes add a stray watermark-like mark or a garbled attempt at a logo
# on its own; this heads that off explicitly rather than just staying
# silent on the subject.
NO_TEXT_INSTRUCTION = (
    "Pure photography only — no text, no logos, no buttons, no watermarks, "
    "and no graphic overlays of any kind anywhere in the image."
)

# Categories where text is never a merchant choice, regardless of what a
# client sends — a category tile is a plain photo, not a marketing graphic
# (see app/api/ai_images.py's resolve_prompt, which forces include_text to
# False server-side for any preset in one of these categories, not just at
# the frontend).
ALWAYS_TEXT_FREE_CATEGORIES = {"category"}

# When a merchant types a free-text prompt with no preset_id, these
# keywords nudge the model toward the same professional composition
# standards a matching preset already encodes — "I want to generate an
# event image" typed straight into the box should not produce a worse
# result than clicking the Events tab and picking one. Checked as a
# case-insensitive substring against the merchant's own words; first match
# wins, so more specific phrases are listed before their broader category.
FREEFORM_STYLE_HINTS: list[tuple[str, str]] = [
    (
        "bento",
        "Compose this like a modern bento-grid multi-cell product "
        "showcase — clean rounded grid dividers, consistent studio "
        "lighting and background tone across every cell.",
    ),
    (
        "category",
        "Compose this like a clean, minimal e-commerce category tile — "
        "centered subject, simple uncluttered background.",
    ),
    (
        "story",
        "Compose this like a professional vertical 9:16 social story "
        "graphic — bold modern composition, premium advertising quality.",
    ),
    (
        "instagram",
        "Compose this like a professional social-media post — bold "
        "modern composition, scroll-stopping, premium advertising "
        "quality.",
    ),
    (
        "social",
        "Compose this like a professional social-media post — bold "
        "modern composition, scroll-stopping, premium advertising "
        "quality.",
    ),
    (
        "banner",
        "Compose this like a premium website hero banner — clean, wide, "
        "professional product photography style.",
    ),
    (
        "hero",
        "Compose this like a premium website hero banner — clean, wide, "
        "professional product photography style.",
    ),
    (
        "promo",
        "Compose this like a professional promo/event announcement "
        "graphic — clean layout, confident composition, premium "
        "advertising quality.",
    ),
    (
        "sale",
        "Compose this like a professional promo/event announcement "
        "graphic — clean layout, confident composition, premium "
        "advertising quality.",
    ),
    (
        "event",
        "Compose this like a professional promo/event announcement "
        "graphic — clean layout, confident composition, premium "
        "advertising quality.",
    ),
]

# Appended to EVERY freeform (no preset_id) generation regardless of
# keyword match — the baseline answer to "the AI should generate nice
# ones" even when nothing above matches what the merchant typed.
FREEFORM_QUALITY_BASELINE = (
    "Professional commercial photography or graphic design quality: "
    "clean composition, well-lit, sharp focus, thoughtful framing — "
    "avoid a cluttered, amateur, or flat result."
)


def freeform_style_hint(prompt: str) -> str | None:
    """First matching FREEFORM_STYLE_HINTS entry for this free-text prompt,
    or None — see app/api/ai_images.py's resolve_prompt, which appends
    whatever this returns (plus FREEFORM_QUALITY_BASELINE either way) when
    the merchant generated without picking a preset."""
    lowered = prompt.lower()
    for keyword, hint in FREEFORM_STYLE_HINTS:
        if keyword in lowered:
            return hint
    return None

PRESET_CATEGORIES: list[dict] = [
    {"id": "product", "label": {"en": "Products", "bn": "প্রোডাক্ট"}},
    {"id": "events", "label": {"en": "Events", "bn": "ইভেন্ট"}},
    {"id": "marketing", "label": {"en": "Marketing", "bn": "মার্কেটিং"}},
    {"id": "category", "label": {"en": "Category", "bn": "ক্যাটাগরি"}},
    {"id": "hero", "label": {"en": "Hero", "bn": "হিরো"}},
    {"id": "bento_showcase", "label": {"en": "Bento", "bn": "বেন্টো"}},
]

IMAGE_PRESETS: list[dict] = [
    # --- Hero ---
    {
        "id": "hero-studio-gradient",
        "category": "hero",
        "name": {"en": "Studio Gradient", "bn": "স্টুডিও গ্রেডিয়েন্ট"},
        "thumbnail": "/ai-presets/hero1.webp",
        "prompt": (
            "A finished, wide website hero banner for an e-commerce storefront "
            "selling {subject}. Soft studio gradient background, with the left "
            "third of the frame kept clear, low-contrast, and free of any busy "
            "detail. Warm natural lighting, professional product photography "
            "style."
        ),
        "text_addon": (
            "On the left third of the frame, include a short bold headline and "
            "a brief supporting line that genuinely fit this specific business "
            "and product (use the real business context given above — do not "
            "invent an unrelated brand), plus a rounded call-to-action button "
            "graphic with short button text appropriate to what's being sold."
        ),
    },
    {
        "id": "hero-lifestyle",
        "category": "hero",
        "name": {"en": "Lifestyle Scene", "bn": "লাইফস্টাইল দৃশ্য"},
        "thumbnail": "/ai-presets/hero2.webp",
        "prompt": (
            "A finished lifestyle website hero banner showing {subject} "
            "naturally in use in a real, warm, well-lit setting. Candid, "
            "editorial photography feel, shallow depth of field, wide aspect "
            "ratio, with one side of the frame kept visually calm and "
            "uncluttered."
        ),
        "text_addon": (
            "On the calmer side of the frame, include a short bold headline "
            "and brief supporting line that genuinely fit this specific "
            "business (use the real business context given above), plus a "
            "rounded call-to-action button graphic with short fitting button "
            "text."
        ),
    },
    {
        "id": "hero-minimal-flatlay",
        "category": "hero",
        "name": {"en": "Minimal Flat Lay", "bn": "মিনিমাল ফ্ল্যাট লে"},
        "thumbnail": "/ai-presets/hero3.webp",
        "prompt": (
            "A finished top-down flat-lay website hero banner featuring "
            "{subject}, arranged with intention on a neutral textured "
            "surface, soft even lighting, minimal color palette, wide banner "
            "composition, with generous empty surface kept clear on one "
            "side."
        ),
        "text_addon": (
            "On the open surface, include a short bold headline and brief "
            "supporting line fitting this specific business (use the real "
            "business context given above), plus a rounded call-to-action "
            "button graphic with short fitting button text."
        ),
    },
    {
        "id": "hero-bold-color",
        "category": "hero",
        "name": {"en": "Bold Color Block", "bn": "বোল্ড কালার ব্লক"},
        "thumbnail": "/ai-presets/hero4.webp",
        "prompt": (
            "A finished, bold, high-contrast website hero banner for "
            "{subject} against a single saturated color background block, "
            "dramatic studio lighting, modern e-commerce advertising style, "
            "wide composition, product placed off-center with a clear "
            "solid-color margin on the other side."
        ),
        "text_addon": (
            "On the solid-color margin, include a short bold headline and "
            "brief supporting line fitting this specific business (use the "
            "real business context given above), plus a rounded "
            "call-to-action button graphic with short fitting button text."
        ),
    },
    # --- Category: plain square photography, no text ever (see
    # ALWAYS_TEXT_FREE_CATEGORIES below) — a category tile is browsed as a
    # normal square box like Product/Events, not cropped into a circle, and
    # never needs copy to be understood. Generic style catalog reusable for
    # ANY category a merchant names via {subject}. ---
    {
        "id": "category-pastel-spotlight",
        "category": "category",
        "name": {"en": "Pastel Spotlight", "bn": "প্যাস্টেল স্পটলাইট"},
        "thumbnail": "/ai-presets/cat-spotlight.webp",
        "prompt": (
            "A square category photo: {subject} placed dead center in the "
            "frame with generous even empty space around it, soft pastel "
            "gradient background filling the whole frame. Soft studio "
            "lighting, subtle shadow beneath, clean minimal premium "
            "e-commerce category-tile style."
        ),
        "text_addon": "",
    },
    {
        "id": "category-cluster",
        "category": "category",
        "name": {"en": "Cluster Arrangement", "bn": "ক্লাস্টার সজ্জা"},
        "thumbnail": "/ai-presets/cat-cluster.webp",
        "prompt": (
            "A square category photo: a small curated cluster of 2-3 items "
            "representing {subject}, grouped tightly together and centered "
            "in the frame, clean neutral background. Soft diffused studio "
            "light."
        ),
        "text_addon": "",
    },
    {
        "id": "category-texture",
        "category": "category",
        "name": {"en": "Textured Surface", "bn": "টেক্সচার্ড সারফেস"},
        "thumbnail": "/ai-presets/cat-texture.webp",
        "prompt": (
            "A square category photo: items representing {subject} "
            "centered on a subtle material texture relevant to the "
            "category, filling the whole frame. Soft warm directional "
            "light, premium boutique feel."
        ),
        "text_addon": "",
    },
    {
        "id": "category-pattern-grid",
        "category": "category",
        "name": {"en": "Pattern Grid", "bn": "প্যাটার্ন গ্রিড"},
        "thumbnail": "/ai-presets/cat-grid.webp",
        "prompt": (
            "A square category photo: several small items representing "
            "{subject} shot from directly overhead, arranged in a neat, "
            "evenly spaced repeating grid pattern filling the frame, "
            "neutral background. Soft even studio lighting."
        ),
        "text_addon": "",
    },
    {
        "id": "category-color-wash",
        "category": "category",
        "name": {"en": "Color Wash", "bn": "কালার ওয়াশ"},
        "thumbnail": "/ai-presets/cat-wash.webp",
        "prompt": (
            "A square category photo: {subject} centered against a bold "
            "saturated gradient color wash background filling the whole "
            "frame. Crisp studio lighting."
        ),
        "text_addon": "",
    },
    {
        "id": "category-framed-badge",
        "category": "category",
        "name": {"en": "Framed Badge", "bn": "ফ্রেমড ব্যাজ"},
        "thumbnail": "/ai-presets/cat-frame.webp",
        "prompt": (
            "A square category photo: {subject} centered inside a thin "
            "gold ring/badge outline, on a soft neutral background. Soft "
            "studio lighting, premium feel."
        ),
        "text_addon": "",
    },
    {
        "id": "category-duotone",
        "category": "category",
        "name": {"en": "Duotone Tile", "bn": "ডুওটোন টাইল"},
        "thumbnail": "/ai-presets/cat-duetone.webp",
        "prompt": (
            "A square category photo: {subject} treated in a single "
            "striking duotone color filter, centered in frame, duotone "
            "background filling the whole frame."
        ),
        "text_addon": "",
    },
    {
        "id": "category-shadow-play",
        "category": "category",
        "name": {"en": "Shadow Play", "bn": "শ্যাডো প্লে"},
        "thumbnail": "/ai-presets/cat-shadow.webp",
        "prompt": (
            "A square category photo: {subject} centered on a plain light "
            "background with one dramatic long shadow cast diagonally from "
            "strong single-source side lighting."
        ),
        "text_addon": "",
    },
    {
        "id": "category-floating-glow",
        "category": "category",
        "name": {"en": "Floating Glow", "bn": "ফ্লোটিং গ্লো"},
        "thumbnail": "/ai-presets/cat-glow.webp",
        "prompt": (
            "A square category photo: {subject} floating slightly above a "
            "soft reflective surface with a gentle glowing halo of light "
            "beneath it, centered in frame. Soft dreamy lighting."
        ),
        "text_addon": "",
    },
    {
        "id": "category-botanical-edge",
        "category": "category",
        "name": {"en": "Botanical Edge", "bn": "বোটানিক্যাল এজ"},
        "thumbnail": "/ai-presets/cat-edge.webp",
        "prompt": (
            "A square category photo: {subject} centered in frame, softly "
            "surrounded by a delicate ring of green botanical leaves and "
            "sprigs framing the edges. Soft natural light."
        ),
        "text_addon": "",
    },
    # --- Product: dramatic environment effects. Same product, one reference
    # photo (REFERENCE_HELPFUL_CATEGORIES already covers "product"), only
    # the surrounding scene/effect changes — the "/underwater"-style
    # eye-catching presets a merchant reaches for when they want something
    # more striking than plain catalog photography. If a reference photo is
    # attached, the product's real shape/color/details must stay accurate;
    # only the environment (and, if opted in, the text) is generated. ---
    {
        "id": "product-golden-hour",
        "category": "product",
        "name": {"en": "Golden Hour Glow", "bn": "গোল্ডেন আওয়ার গ্লো"},
        "thumbnail": "/ai-presets/golden.webp",
        "prompt": (
            "A warm cinematic product photo of {subject} bathed in "
            "golden-hour sunlight, soft warm rim light outlining the edges, "
            "gentle lens flare, dreamy amber atmosphere. If a reference "
            "photo of the product is provided, keep its real shape, color, "
            "and details accurate — only the lighting and atmosphere are "
            "generated. Sharp focus on the product, cinematic and "
            "eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the frame, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-ice-frost",
        "category": "product",
        "name": {"en": "Ice & Frost", "bn": "বরফ ও তুষার"},
        "thumbnail": "/ai-presets/ice.webp",
        "prompt": (
            "A striking product photo of {subject} encased in a thin layer "
            "of frost with delicate ice crystals forming around it, cool "
            "blue-white lighting, a light mist drifting past. If a "
            "reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the frost and "
            "atmosphere are generated. Sharp focus on the product, "
            "cinematic and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the frame, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-confetti",
        "category": "product",
        "name": {"en": "Confetti Celebration", "bn": "কনফেটি উদযাপন"},
        "thumbnail": "/ai-presets/celebrate.webp",
        "prompt": (
            "An energetic product photo of {subject} frozen mid-air "
            "surrounded by a colorful burst of confetti and streamers "
            "caught in motion, vibrant festive studio lighting against a "
            "bright background. If a reference photo of the product is "
            "provided, keep its real shape, color, and details accurate — "
            "only the confetti effect is generated. Sharp focus on the "
            "product, joyful and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the frame, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-glossy-reflection",
        "category": "product",
        "name": {"en": "Glossy Reflection", "bn": "গ্লসি প্রতিফলন"},
        "thumbnail": "/ai-presets/reflect.webp",
        "prompt": (
            "A moody product photo of {subject} standing on a glossy black "
            "reflective surface, dramatic single-source studio lighting, a "
            "crisp mirror reflection beneath it fading into darkness. If a "
            "reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the reflective "
            "surface and lighting are generated. Sharp focus on the "
            "product, premium and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear dark area, genuinely fitting this "
            "specific business (use the real business context given "
            "above)."
        ),
    },
    {
        "id": "product-cosmic-float",
        "category": "product",
        "name": {"en": "Cosmic Float", "bn": "কসমিক ফ্লোট"},
        "thumbnail": "/ai-presets/galaxy.webp",
        "prompt": (
            "A dramatic product photo of {subject} floating in a starry "
            "cosmic nebula scene, deep purples and blues with scattered "
            "stars and soft glowing light, a sense of weightlessness. If a "
            "reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the cosmic "
            "background is generated. Sharp focus on the product, striking "
            "and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the nebula, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-rain-glass",
        "category": "product",
        "name": {"en": "Rain-Streaked Glass", "bn": "বৃষ্টিভেজা কাচ"},
        "thumbnail": "/ai-presets/rain.webp",
        "prompt": (
            "A cinematic product photo of {subject} shot through a "
            "rain-streaked window, soft bokeh city lights blurred in the "
            "background, moody blue-toned lighting, water droplets in "
            "sharp focus near the lens. If a reference photo of the "
            "product is provided, keep its real shape, color, and details "
            "accurate — only the rain/window effect is generated. Sharp "
            "focus on the product, moody and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the frame, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-underwater",
        "category": "product",
        "name": {"en": "Underwater", "bn": "আন্ডারওয়াটার"},
        "thumbnail": "/ai-presets/underwater.webp",
        "prompt": (
            "A striking product photo of {subject} submerged in clear blue "
            "water, surrounded by fine bubbles rising past it, soft caustic "
            "light rays filtering down from above, dreamy underwater "
            "atmosphere. If a reference photo of the product is provided, "
            "keep its real shape, color, and details accurate — only the "
            "surrounding water and light are generated. Sharp focus on the "
            "product, cinematic and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic positioned in the clearer upper portion of the "
            "frame, genuinely fitting this specific business (use the real "
            "business context given above)."
        ),
    },
    {
        "id": "product-cloud-float",
        "category": "product",
        "name": {"en": "Floating on Clouds", "bn": "মেঘে ভাসমান"},
        "thumbnail": "/ai-presets/cloud.webp",
        "prompt": (
            "A dreamy product photo of {subject} floating weightlessly "
            "among soft pastel clouds against a pale sky, gentle "
            "golden-hour light. If a reference photo of the product is "
            "provided, keep its real shape, color, and details accurate — "
            "only the surrounding clouds and sky are generated. Sharp "
            "focus on the product, whimsical and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic positioned in the open sky area, genuinely "
            "fitting this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "product-splash-burst",
        "category": "product",
        "name": {"en": "Liquid Splash", "bn": "লিকুইড স্প্ল্যাশ"},
        "thumbnail": "/ai-presets/splash.webp",
        "prompt": (
            "A high-energy product photo of {subject} frozen mid-air at "
            "the exact moment of a dynamic liquid splash bursting around "
            "it, droplets frozen in motion, dramatic studio lighting "
            "against a dark background. If a reference photo of the "
            "product is provided, keep its real shape, color, and details "
            "accurate — only the splash effect is generated. Sharp focus "
            "on the product, high-impact advertising style."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in a clear area of the dark background, "
            "genuinely fitting this specific business (use the real "
            "business context given above)."
        ),
    },
    {
        "id": "product-desert-dunes",
        "category": "product",
        "name": {"en": "Desert Dunes", "bn": "মরুভূমির টিলা"},
        "thumbnail": "/ai-presets/desert.webp",
        "prompt": (
            "A cinematic product photo of {subject} resting on golden "
            "desert sand dunes at sunset, warm dramatic side light, long "
            "soft shadows, vast dune landscape stretching into a hazy "
            "horizon. If a reference photo of the product is provided, "
            "keep its real shape, color, and details accurate — only the "
            "desert scene is generated. Sharp focus on the product, "
            "striking and eye-catching."
        ),
        "text_addon": (
            "Include a short bold headline and a rounded call-to-action "
            "button graphic in the open sky area, genuinely fitting this "
            "specific business (use the real business context given "
            "above)."
        ),
    },
    # --- Product Bento ---
    {
        "id": "bento-multi-angle",
        "category": "bento_showcase",
        "name": {"en": "Multi-Angle Bento", "bn": "মাল্টি-অ্যাঙ্গেল বেন্টো"},
        "thumbnail": "/ai-presets/bento1.webp",
        "prompt": (
            "A modern bento-grid product showcase banner for {subject}: "
            "one large cell showing the full product clearly, surrounded "
            "by 3-4 smaller cells in the same grid each highlighting a "
            "different angle, a close-up material/detail shot, and the "
            "product in real use. Clean rounded grid dividers between "
            "cells, consistent soft studio lighting and matching neutral "
            "background tone across every cell so it reads as one cohesive "
            "banner, premium modern social/product-page layout."
        ),
        "text_addon": (
            "Each cell has a short bold caption baked into its corner "
            "naming what it shows (e.g. 'Full View', 'Close-Up', "
            "'In Use')."
        ),
    },
    {
        "id": "bento-lifestyle-detail",
        "category": "bento_showcase",
        "name": {"en": "Lifestyle + Detail Bento", "bn": "লাইফস্টাইল + ডিটেইল বেন্টো"},
        "thumbnail": "/ai-presets/bento2.webp",
        "prompt": (
            "A bento-grid product banner for {subject} mixing one larger "
            "lifestyle in-use cell with several smaller studio "
            "close-up/detail cells around it. Consistent warm color "
            "grading and lighting across every cell, clean rounded grid "
            "dividers, premium cohesive e-commerce banner layout."
        ),
        "text_addon": (
            "Each cell has a short bold caption baked into its corner "
            "naming what it shows (e.g. 'In Use', 'Detail', 'Texture')."
        ),
    },
    {
        "id": "bento-scale-texture",
        "category": "bento_showcase",
        "name": {"en": "Scale + Texture Bento", "bn": "স্কেল + টেক্সচার বেন্টো"},
        "thumbnail": "/ai-presets/bento3.webp",
        "prompt": (
            "A bento-grid product banner for {subject} with an asymmetric "
            "layout: one larger cell showing the full product clearly, "
            "paired with smaller cells showing a close-up macro shot of "
            "its material/texture detail and the product held or worn for "
            "a sense of real scale. Consistent soft studio lighting and "
            "neutral background tone across every cell, clean rounded grid "
            "dividers, premium cohesive layout."
        ),
        "text_addon": (
            "Each cell has a short bold caption baked into its corner "
            "naming what it shows (e.g. 'Full View', 'Texture', "
            "'True to Scale')."
        ),
    },
    # --- Events / Promo ---
    {
        "id": "event-editorial-minimal",
        "category": "events",
        "name": {"en": "Editorial Minimal", "bn": "এডিটোরিয়াল মিনিমাল"},
        "thumbnail": "/ai-presets/event1.webp",
        "prompt": (
            "A premium minimal promo graphic for {subject}: a warm "
            "cream/off-white background with generous negative space, one "
            "small elegant product photo in the lower third. Sophisticated "
            "boutique editorial aesthetic — no gradients, no starbursts, "
            "no neon. 1:1."
        ),
        "text_addon": (
            "A large refined modern sans-serif headline across the upper "
            "two-thirds and a short thin-outlined pill button beneath it "
            "should genuinely fit this specific business (use the real "
            "business context given above)."
        ),
    },
    {
        "id": "event-duotone-drop",
        "category": "events",
        "name": {"en": "Duotone Drop", "bn": "ডুওটোন ড্রপ"},
        "thumbnail": "/ai-presets/event2.webp",
        "prompt": (
            "A bold modern promo graphic for {subject} styled like a "
            "streetwear product-drop announcement: the product photo "
            "treated in a single striking duotone color filter filling the "
            "left half of the frame, a solid matching dark color block on "
            "the right half. High contrast, confident modern energy, no "
            "soft gradients. 1:1."
        ),
        "text_addon": (
            "On the right color block, include large condensed bold "
            "headline text and a sharp rectangular CTA button — copy "
            "should genuinely fit this specific business (use the real "
            "business context given above)."
        ),
    },
    {
        "id": "event-glass-card",
        "category": "events",
        "name": {"en": "Glass Card", "bn": "গ্লাস কার্ড"},
        "thumbnail": "/ai-presets/event3.webp",
        "prompt": (
            "A modern promo graphic for {subject}: a full-bleed soft-focus "
            "product photo fills the entire frame, with a frosted "
            "glassmorphism card (translucent white, blurred backdrop, thin "
            "light border, subtle shadow) floating centered over it. "
            "Trendy modern app-UI aesthetic, soft ambient lighting behind "
            "the glass card. 1:1."
        ),
        "text_addon": (
            "Inside the glass card, include a bold headline and a solid "
            "rounded CTA button — copy should genuinely fit this specific "
            "business (use the real business context given above)."
        ),
    },
    {
        "id": "event-bold-type-poster",
        "category": "events",
        "name": {"en": "Bold Type Poster", "bn": "বোল্ড টাইপ পোস্টার"},
        "thumbnail": "/ai-presets/event4.webp",
        "prompt": (
            "A high-impact typographic promo poster for {subject}: the "
            "product photo integrated behind or within negative space, "
            "two-tone black-and-white or single-accent-color palette. "
            "Editorial fashion-poster energy, confident and graphic. 1:1."
        ),
        "text_addon": (
            "Massive oversized bold headline text fills most of the frame "
            "edge to edge as the dominant visual element — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above) — with a small solid CTA button tucked "
            "in the bottom corner."
        ),
    },
    {
        "id": "event-color-block-split",
        "category": "events",
        "name": {"en": "Color Block Split", "bn": "কালার ব্লক স্প্লিট"},
        "thumbnail": "/ai-presets/event5.webp",
        "prompt": (
            "A modern flat-design promo graphic for {subject}: the frame "
            "split cleanly into two solid color blocks (a confident "
            "two-tone palette, no gradients, no photo treatment), the "
            "product photo cut out cleanly on one side. Flat, graphic, "
            "poster-like confidence, sharp clean edges between the color "
            "blocks. 1:1."
        ),
        "text_addon": (
            "On the other side, include a large bold headline and a solid "
            "rectangular CTA button — copy should genuinely fit this "
            "specific business (use the real business context given "
            "above)."
        ),
    },
    {
        "id": "event-framed-spotlight",
        "category": "events",
        "name": {"en": "Framed Spotlight", "bn": "ফ্রেমড স্পটলাইট"},
        "thumbnail": "/ai-presets/event6.webp",
        "prompt": (
            "A premium promo graphic for {subject}: the product photo sits "
            "centered within a thin rounded rectangular frame/border (like "
            "a museum label or gallery spotlight), soft focused lighting "
            "on the product inside the frame. Clean neutral background "
            "outside the frame, elegant gallery-retail aesthetic, no "
            "clutter. 1:1."
        ),
        "text_addon": (
            "Below the frame, include a refined headline and a small "
            "outlined CTA button — copy should genuinely fit this specific "
            "business (use the real business context given above)."
        ),
    },
    # --- Marketing / Social ---
    {
        "id": "marketing-minimal-drop",
        "category": "marketing",
        "name": {"en": "Minimal Drop", "bn": "মিনিমাল ড্রপ"},
        "thumbnail": "/ai-presets/minimal.webp",
        "prompt": (
            "A premium minimal product-drop social post for {subject}: a "
            "single product centered against a soft solid pastel "
            "background with generous negative space. Understated "
            "branding-forward aesthetic — no gradients, no bursts, no "
            "clutter. 1:1."
        ),
        "text_addon": (
            "A small confident caption in sleek modern sans-serif text in "
            "the lower corner — copy should genuinely fit this specific "
            "business (use the real business context given above)."
        ),
    },
    {
        "id": "marketing-duotone-story",
        "category": "marketing",
        "name": {"en": "Duotone Story", "bn": "ডুওটোন স্টোরি"},
        "thumbnail": "/ai-presets/duotone.webp",
        "prompt": (
            "A bold vertical 9:16 Instagram/Facebook Story graphic for "
            "{subject}: the product photo treated in a single striking "
            "duotone color filter filling most of the frame. "
            "High-contrast editorial-fashion energy — no soft gradients, "
            "no clip-art icons."
        ),
        "text_addon": (
            "A large confident statement headline in sleek modern bold "
            "type in the lower third over a solid-color strip for "
            "legibility — copy should genuinely fit this specific business "
            "(use the real business context given above)."
        ),
    },
    {
        "id": "marketing-collage-grid",
        "category": "marketing",
        "name": {"en": "Collage Grid", "bn": "কোলাজ গ্রিড"},
        "thumbnail": "/ai-presets/collagegrid.webp",
        "prompt": (
            "A modern collage-style social post for {subject}: three to "
            "four photos of the product from different angles or contexts "
            "arranged in an organic overlapping collage (not a rigid "
            "grid). Cohesive color grading across all photos, clean modern "
            "editorial feel. 1:1."
        ),
        "text_addon": (
            "One bold pull-quote style headline in sleek modern large type "
            "overlapping the collage at a confident angle — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above)."
        ),
    },
    {
        "id": "marketing-glass-caption",
        "category": "marketing",
        "name": {"en": "Glass Caption", "bn": "গ্লাস ক্যাপশন"},
        "thumbnail": "/ai-presets/glasscaption.webp",
        "prompt": (
            "A modern social post for {subject}: a full-bleed lifestyle "
            "photo of the product fills the entire square frame. Trendy "
            "modern app-UI aesthetic, soft ambient lighting. 1:1."
        ),
        "text_addon": (
            "A frosted glassmorphism caption bar (translucent white, "
            "blurred backdrop, thin light border) across the bottom third "
            "containing a short bold headline in sleek modern clean type "
            "— copy should genuinely fit this specific business (use the "
            "real business context given above)."
        ),
    },
    {
        "id": "marketing-neon-sign",
        "category": "marketing",
        "name": {"en": "Neon Sign", "bn": "নিয়ন সাইন"},
        "thumbnail": "/ai-presets/neon.webp",
        "prompt": (
            "A bold social media post for {subject}: the product lit by "
            "glowing pink and blue neon light against a dark moody "
            "nightlife backdrop. Moody urban energy. 1:1."
        ),
        "text_addon": (
            "A headline rendered as an actual glowing neon-tube sign and a "
            "smaller neon-outline CTA button beneath it — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above)."
        ),
    },
    {
        "id": "marketing-polaroid-frame",
        "category": "marketing",
        "name": {"en": "Polaroid Frame", "bn": "পোলারয়েড ফ্রেম"},
        "thumbnail": "/ai-presets/frame.webp",
        "prompt": (
            "A trendy social post for {subject} styled as a single "
            "polaroid photograph: the product shot in warm natural light, "
            "framed with the classic white polaroid border. Subtle drop "
            "shadow beneath the polaroid on a soft neutral background, "
            "sleek modern aesthetic. 1:1."
        ),
        "text_addon": (
            "A short handwritten-style caption scrawled in the bottom "
            "white margin in a casual script font — copy should genuinely "
            "fit this specific business (use the real business context "
            "given above)."
        ),
    },
    {
        "id": "marketing-phone-mockup",
        "category": "marketing",
        "name": {"en": "Phone Mockup", "bn": "ফোন মকআপ"},
        "thumbnail": "/ai-presets/insta.webp",
        "prompt": (
            "A modern social post for {subject} showing a realistic "
            "smartphone mockup centered in frame, its screen displaying an "
            "Instagram-style post of the product with heart/comment icons "
            "visible on-screen, soft gradient background behind the "
            "phone. Premium tech-forward feel. 1:1."
        ),
        "text_addon": (
            "Above the phone, include a bold headline in sleek modern type "
            "— copy should genuinely fit this specific business (use the "
            "real business context given above)."
        ),
    },
    {
        "id": "marketing-countdown",
        "category": "marketing",
        "name": {"en": "Countdown Urgency", "bn": "কাউন্টডাউন"},
        "thumbnail": "/ai-presets/countdown.webp",
        "prompt": (
            "A vertical 9:16 Instagram/Facebook Story graphic for "
            "{subject} on a bold solid-color background. Sleek modern "
            "high-urgency energetic color palette."
        ),
        "text_addon": (
            "Include a large sleek countdown-style badge and a bold "
            "headline below it plus a rounded CTA button — copy should "
            "genuinely fit this specific business and convey urgency (use "
            "the real business context given above)."
        ),
    },
    {
        "id": "marketing-split-duo",
        "category": "marketing",
        "name": {"en": "Split Duo", "bn": "স্প্লিট ডুও"},
        "thumbnail": "/ai-presets/duo.webp",
        "prompt": (
            "A modern social post for {subject} split cleanly down the "
            "middle: the product shown one way on the left half against a "
            "light background, and a contrasting variant/angle on the "
            "right half against a dark background, with a thin vertical "
            "divider line between the two halves. Confident graphic "
            "layout. 1:1."
        ),
        "text_addon": (
            "A bold headline spanning both halves at the top in sleek "
            "modern type — copy should genuinely fit this specific "
            "business (use the real business context given above)."
        ),
    },
    {
        "id": "marketing-testimonial-quote",
        "category": "marketing",
        "name": {"en": "Testimonial Quote", "bn": "টেস্টিমোনিয়াল কোট"},
        "thumbnail": "/ai-presets/quote.webp",
        "prompt": (
            "A modern social post for {subject} shot in soft natural light "
            "filling the frame. Premium social-proof aesthetic. 1:1."
        ),
        "text_addon": (
            "A frosted glassmorphism quote card overlapping the lower "
            "third containing a short five-star review snippet in sleek "
            "modern type and the business name beneath it — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above)."
        ),
    },
]


def presets_by_category() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {c["id"]: [] for c in PRESET_CATEGORIES}
    for preset in IMAGE_PRESETS:
        grouped.setdefault(preset["category"], []).append(preset)
    return grouped


def get_preset(preset_id: str) -> dict | None:
    return next((p for p in IMAGE_PRESETS if p["id"] == preset_id), None)
