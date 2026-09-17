"""Preset prompts for AI image generation — the "/underwater"-style picker
the merchant browses instead of writing a prompt from scratch.

Deliberately a plain Python list, not a database table: these are curated
content the team writes (often with a different LLM's help, per how this
was scoped), not user data, so they ship with a code deploy like the theme
catalog does, not through an admin CRUD screen.

Each preset's `thumbnail` is a path served from the dashboard's own
`public/ai-presets/` folder — drop a PNG there with the matching filename
and it shows up; nothing here reads image bytes, this is just the prompt
+ metadata half of the pair. `prompt` uses `{subject}` as the one
substitution point for whatever the merchant is actually generating for
(a product name, a store name, a category name) — see
app/api/ai_images.py's `_render_preset_prompt`.

Categories mirror the site-editor sections a generated image is destined
for (hero/category/product) plus three standalone-graphic buckets that
never get placed under a dynamic text overlay: `bento_showcase` (a
multi-cell product-angle grid banner), `events` (a promo/announcement
graphic with its own headline + CTA button baked in), and `marketing`
(social posts/ad creative). Ten presets per category is the target scope;
each category below ships with a real, working starter set — extend
freely, this file is the only place that needs an edit.

TEXT POLICY — read before adding a preset: every category here produces a
FINISHED, standalone graphic with real copy baked directly into the pixels
— headline, supporting line, per-cell caption, and/or a CTA button where
the layout calls for one. Nothing is left blank for a separate overlay:
`hero` gets its headline/button text from the real business context
(app/api/ai_images.py's `_business_context_line`, pulled from the
merchant's own site.business name/description) plus whatever the merchant
typed for `subject` or as free-text instructions. `bento_showcase` is a
multi-cell collage, so a plain photo grid with no labels just reads as
random product shots — each cell gets a short caption naming what it
shows (a color, an angle, "detail," "true to scale," etc.) so the banner
actually communicates something. `events`/`marketing` already work the same way, and so does `product` —
its ten presets are presentation-style creative shots (underwater, ice,
confetti, etc.), not plain catalog photography, so they carry a
business-context headline + CTA button the same as hero. Only `category`
stays plain photography — a category tile doesn't need copy to be
understood — but if a merchant's own instructions ask for text there too,
that's honored the same way. The one hard rule for EVERY preset: whenever
a prompt does
include text, it must ask explicitly for crisp, correctly spelled,
legible typography — an image-generation model told nothing about text
usually leaves it out or garbles it if it guesses on its own.
"""

PRESET_CATEGORIES: list[dict] = [
    {"id": "events", "label": {"en": "Events", "bn": "ইভেন্ট"}},
    {"id": "marketing", "label": {"en": "Marketing", "bn": "মার্কেটিং"}},
    {"id": "product", "label": {"en": "Products", "bn": "প্রোডাক্ট"}},
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
            "selling {subject}. Soft studio gradient background. On the left "
            "third of the frame, include a short, bold, correctly spelled "
            "headline and a brief supporting line that genuinely fit this "
            "specific business and product (use the real business context given "
            "above — do not invent an unrelated brand), plus a rounded "
            "call-to-action button graphic with short button text appropriate to "
            "what's being sold. Warm natural lighting, professional product "
            "photography style, crisp legible typography baked directly into the "
            "image — this is the finished banner, nothing is added afterward."
        ),
    },
    {
        "id": "hero-lifestyle",
        "category": "hero",
        "name": {"en": "Lifestyle Scene", "bn": "লাইফস্টাইল দৃশ্য"},
        "thumbnail": "/ai-presets/hero2.webp",
        "prompt": (
            "A finished lifestyle website hero banner showing {subject} naturally "
            "in use in a real, warm, well-lit setting. Candid, editorial "
            "photography feel, shallow depth of field, wide aspect ratio. On the "
            "calmer, less busy side of the frame, include a short bold correctly "
            "spelled headline and brief supporting line that genuinely fit this "
            "specific business (use the real business context given above), plus "
            "a rounded call-to-action button graphic with short fitting button "
            "text. Crisp legible typography baked directly into the image — this "
            "is the finished banner, nothing is added afterward."
        ),
    },
    {
        "id": "hero-minimal-flatlay",
        "category": "hero",
        "name": {"en": "Minimal Flat Lay", "bn": "মিনিমাল ফ্ল্যাট লে"},
        "thumbnail": "/ai-presets/hero3.webp",
        "prompt": (
            "A finished top-down flat-lay website hero banner featuring "
            "{subject}, arranged with intention on a neutral textured surface, "
            "soft even lighting, minimal color palette, wide banner composition. "
            "On the open surface to one side, include a short bold correctly "
            "spelled headline and brief supporting line fitting this specific "
            "business (use the real business context given above), plus a "
            "rounded call-to-action button graphic with short fitting button "
            "text. Crisp legible typography baked directly into the image — this "
            "is the finished banner, nothing is added afterward."
        ),
    },
    {
        "id": "hero-bold-color",
        "category": "hero",
        "name": {"en": "Bold Color Block", "bn": "বোল্ড কালার ব্লক"},
        "thumbnail": "/ai-presets/hero4.webp",
        "prompt": (
            "A finished, bold, high-contrast website hero banner for {subject} "
            "against a single saturated color background block, dramatic studio "
            "lighting, modern e-commerce advertising style, wide composition, "
            "product placed off-center. On the solid-color margin, include a "
            "short bold correctly spelled headline and brief supporting line "
            "fitting this specific business (use the real business context given "
            "above), plus a rounded call-to-action button graphic with short "
            "fitting button text. Crisp legible typography baked directly into "
            "the image — this is the finished banner, nothing is added "
            "afterward."
        ),
    },
    # --- Category ---
    {
        "id": "category-icon-style",
        "category": "category",
        "name": {"en": "Soft Icon Style", "bn": "সফট আইকন স্টাইল"},
        "thumbnail": "/ai-presets/cat1.webp",
        "prompt": (
            "A square category thumbnail representing {subject}, centered "
            "composition, soft pastel background, subtle shadow, clean modern "
            "e-commerce category tile style, no text."
        ),
    },
    {
        "id": "category-product-cluster",
        "category": "category",
        "name": {"en": "Product Cluster", "bn": "প্রোডাক্ট ক্লাস্টার"},
        "thumbnail": "/ai-presets/cat2.webp",
        "prompt": (
            "A square image showing a small curated cluster of items representing "
            "the {subject} category, neatly arranged, soft studio lighting, "
            "neutral background, e-commerce category card style, no text."
        ),
    },
    {
        "id": "category-textured-bg",
        "category": "category",
        "name": {"en": "Textured Background", "bn": "টেক্সচার্ড ব্যাকগ্রাউন্ড"},
        "thumbnail": "/ai-presets/cat3.webp",
        "prompt": (
            "A square category image for {subject} set against a subtle textured "
            "material background relevant to the category, soft directional "
            "light, minimal and premium e-commerce feel, no text."
        ),
    },
    {
        "id": "category-pattern-grid",
        "category": "category",
        "name": {"en": "Pattern Grid", "bn": "প্যাটার্ন গ্রিড"},
        "thumbnail": "/ai-presets/cat4.webp",
        "prompt": (
            "A square category image for {subject}: several small identical or "
            "closely related items shot from directly overhead, arranged in a "
            "neat, evenly spaced repeating grid so it reads as a clean pattern "
            "rather than a cluster, soft even studio lighting, neutral "
            "background, minimal premium e-commerce category style, no text."
        ),
    },
    # --- Product: dramatic environment effects. Same product, one reference
    # photo (REFERENCE_HELPFUL_CATEGORIES already covers "product"), only the
    # surrounding scene/effect changes — the "/underwater"-style eye-catching
    # presets a merchant reaches for when they want something more striking
    # than plain catalog photography. If a reference photo is attached, the
    # product's real shape/color/details must stay accurate; only the
    # environment and text around it are generated. ---
    {
        "id": "product-underwater",
        "category": "product",
        "name": {"en": "Underwater", "bn": "আন্ডারওয়াটার"},
        "thumbnail": "/ai-presets/prod1.webp",
        "prompt": (
            "A striking product photo of {subject} submerged in clear blue "
            "water, surrounded by fine bubbles rising past it, soft caustic "
            "light rays filtering down from above, dreamy underwater "
            "atmosphere. Include a short bold correctly spelled headline and "
            "a rounded call-to-action button graphic positioned in the "
            "clearer upper portion of the frame, genuinely fitting this "
            "specific business (use the real business context given above). "
            "If a reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the environment and "
            "text are generated. Sharp focus on the product, cinematic and "
            "eye-catching, sleek, modern, crisp legible typography."
        ),
    },
    {
        "id": "product-cloud-float",
        "category": "product",
        "name": {"en": "Floating on Clouds", "bn": "মেঘে ভাসমান"},
        "thumbnail": "/ai-presets/prod2.webp",
        "prompt": (
            "A dreamy product photo of {subject} floating weightlessly among "
            "soft pastel clouds against a pale sky, gentle golden-hour light. "
            "Include a short bold correctly spelled headline and a rounded "
            "call-to-action button graphic positioned in the open sky area, "
            "genuinely fitting this specific business (use the real business "
            "context given above). If a reference photo of the product is "
            "provided, keep its real shape, color, and details accurate — "
            "only the environment and text are generated. Sharp focus on the "
            "product, whimsical and eye-catching, sleek, modern, crisp legible typography."
        ),
    },
    {
        "id": "product-splash-burst",
        "category": "product",
        "name": {"en": "Liquid Splash", "bn": "লিকুইড স্প্ল্যাশ"},
        "thumbnail": "/ai-presets/prod3.webp",
        "prompt": (
            "A high-energy product photo of {subject} frozen mid-air at the "
            "exact moment of a dynamic liquid splash bursting around it, "
            "droplets frozen in motion, dramatic studio lighting against a "
            "dark background. Include a short bold correctly spelled headline "
            "and a rounded call-to-action button graphic in a clear area of "
            "the dark background, genuinely fitting this specific business "
            "(use the real business context given above). If a reference "
            "photo of the product is provided, keep its real shape, color, "
            "and details accurate — only the splash effect and text are "
            "generated. Sharp focus on the product, high-impact advertising "
            "style, sleek, modern, crisp legible typography."
        ),
    },
    {
        "id": "product-desert-dunes",
        "category": "product",
        "name": {"en": "Desert Dunes", "bn": "মরুভূমির টিলা"},
        "thumbnail": "/ai-presets/prod4.webp",
        "prompt": (
            "A cinematic product photo of {subject} resting on golden desert "
            "sand dunes at sunset, warm dramatic side light, long soft "
            "shadows, vast dune landscape stretching into a hazy horizon. "
            "Include a short bold correctly spelled headline and a rounded "
            "call-to-action button graphic in the open sky area, genuinely "
            "fitting this specific business (use the real business context "
            "given above). If a reference photo of the product is provided, "
            "keep its real shape, color, and details accurate — only the "
            "desert scene and text are generated. Sharp focus on the "
            "product, striking and eye-catching, sleek, modern, crisp legible typography."
        ),
    },
    {
        "id": "product-golden-hour",
        "category": "product",
        "name": {"en": "Golden Hour Glow", "bn": "গোল্ডেন আওয়ার গ্লো"},
        "thumbnail": "/ai-presets/prod5.webp",
        "prompt": (
            "A warm cinematic product photo of {subject} bathed in "
            "golden-hour sunlight, soft warm rim light outlining the edges, "
            "gentle lens flare, dreamy amber atmosphere. Include a short "
            "bold correctly spelled headline and a rounded call-to-action "
            "button graphic in a clear area of the frame, genuinely fitting "
            "this specific business (use the real business context given "
            "above). If a reference photo of the product is provided, keep "
            "its real shape, color, and details accurate — only the "
            "lighting, atmosphere, and text are generated. Sharp focus on "
            "the product, cinematic and eye-catching, sleek, modern, crisp "
            "legible typography."
        ),
    },
    {
        "id": "product-ice-frost",
        "category": "product",
        "name": {"en": "Ice & Frost", "bn": "বরফ ও তুষার"},
        "thumbnail": "/ai-presets/prod6.webp",
        "prompt": (
            "A striking product photo of {subject} encased in a thin layer "
            "of frost with delicate ice crystals forming around it, cool "
            "blue-white lighting, a light mist drifting past. Include a "
            "short bold correctly spelled headline and a rounded "
            "call-to-action button graphic in a clear area of the frame, "
            "genuinely fitting this specific business (use the real "
            "business context given above). If a reference photo of the "
            "product is provided, keep its real shape, color, and details "
            "accurate — only the frost, atmosphere, and text are generated. "
            "Sharp focus on the product, cinematic and eye-catching, sleek, "
            "modern, crisp legible typography."
        ),
    },
    {
        "id": "product-confetti",
        "category": "product",
        "name": {"en": "Confetti Celebration", "bn": "কনফেটি উদযাপন"},
        "thumbnail": "/ai-presets/prod7.webp",
        "prompt": (
            "An energetic product photo of {subject} frozen mid-air "
            "surrounded by a colorful burst of confetti and streamers "
            "caught in motion, vibrant festive studio lighting against a "
            "bright background. Include a short bold correctly spelled "
            "headline and a rounded call-to-action button graphic in a "
            "clear area of the frame, genuinely fitting this specific "
            "business (use the real business context given above). If a "
            "reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the confetti effect "
            "and text are generated. Sharp focus on the product, joyful and "
            "eye-catching, sleek, modern, crisp legible typography."
        ),
    },
    {
        "id": "product-glossy-reflection",
        "category": "product",
        "name": {"en": "Glossy Reflection", "bn": "গ্লসি প্রতিফলন"},
        "thumbnail": "/ai-presets/prod8.webp",
        "prompt": (
            "A moody product photo of {subject} standing on a glossy black "
            "reflective surface, dramatic single-source studio lighting, a "
            "crisp mirror reflection beneath it fading into darkness. "
            "Include a short bold correctly spelled headline and a rounded "
            "call-to-action button graphic in a clear dark area, genuinely "
            "fitting this specific business (use the real business context "
            "given above). If a reference photo of the product is provided, "
            "keep its real shape, color, and details accurate — only the "
            "reflective surface, lighting, and text are generated. Sharp "
            "focus on the product, premium and eye-catching, sleek, modern, "
            "crisp legible typography."
        ),
    },
    {
        "id": "product-cosmic-float",
        "category": "product",
        "name": {"en": "Cosmic Float", "bn": "কসমিক ফ্লোট"},
        "thumbnail": "/ai-presets/prod9.webp",
        "prompt": (
            "A dramatic product photo of {subject} floating in a starry "
            "cosmic nebula scene, deep purples and blues with scattered "
            "stars and soft glowing light, a sense of weightlessness. "
            "Include a short bold correctly spelled headline and a rounded "
            "call-to-action button graphic in a clear area of the nebula, "
            "genuinely fitting this specific business (use the real "
            "business context given above). If a reference photo of the "
            "product is provided, keep its real shape, color, and details "
            "accurate — only the cosmic background and text are generated. "
            "Sharp focus on the product, striking and eye-catching, sleek, "
            "modern, crisp legible typography."
        ),
    },
    {
        "id": "product-rain-glass",
        "category": "product",
        "name": {"en": "Rain-Streaked Glass", "bn": "বৃষ্টিভেজা কাচ"},
        "thumbnail": "/ai-presets/prod10.webp",
        "prompt": (
            "A cinematic product photo of {subject} shot through a "
            "rain-streaked window, soft bokeh city lights blurred in the "
            "background, moody blue-toned lighting, water droplets in "
            "sharp focus near the lens. Include a short bold correctly "
            "spelled headline and a rounded call-to-action button graphic "
            "in a clear area of the frame, genuinely fitting this specific "
            "business (use the real business context given above). If a "
            "reference photo of the product is provided, keep its real "
            "shape, color, and details accurate — only the rain/window "
            "effect and text are generated. Sharp focus on the product, "
            "moody and eye-catching, sleek, modern, crisp legible typography."
        ),
    },
    # --- Product Bento ---
    {
        "id": "bento-multi-angle",
        "category": "bento_showcase",
        "name": {"en": "Multi-Angle Bento", "bn": "মাল্টি-অ্যাঙ্গেল বেন্টো"},
        "thumbnail": "/ai-presets/bento1.webp",
        "prompt": (
            "A modern bento-grid product showcase banner for {subject}: one large "
            "cell showing the full product clearly, surrounded by 3-4 smaller "
            "cells in the same grid each highlighting a different angle, a "
            "close-up material/detail shot, and the product in real use. Each "
            "cell has a short bold correctly spelled caption baked into its "
            "corner naming what it shows (e.g. 'Full View', 'Close-Up', "
            "'In Use'). Clean rounded grid dividers between cells, consistent "
            "soft studio lighting and matching neutral background tone across "
            "every cell so it reads as one cohesive banner, premium modern "
            "social/product-page layout, crisp legible typography."
        ),
    },
    {
        "id": "bento-lifestyle-detail",
        "category": "bento_showcase",
        "name": {"en": "Lifestyle + Detail Bento", "bn": "লাইফস্টাইল + ডিটেইল বেন্টো"},
        "thumbnail": "/ai-presets/bento2.webp",
        "prompt": (
            "A bento-grid product banner for {subject} mixing one larger "
            "lifestyle in-use cell with several smaller studio close-up/detail "
            "cells around it. Each cell has a short bold correctly spelled "
            "caption baked into its corner naming what it shows (e.g. "
            "'In Use', 'Detail', 'Texture'). Consistent warm color grading and "
            "lighting across every cell, clean rounded grid dividers, premium "
            "cohesive e-commerce banner layout, crisp legible typography."
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
            "paired with smaller cells showing a close-up macro shot of its "
            "material/texture detail and the product held or worn for a "
            "sense of real scale. Each cell has a short bold correctly "
            "spelled caption baked into its corner naming what it shows "
            "(e.g. 'Full View', 'Texture', 'True to Scale'). Consistent soft "
            "studio lighting and neutral background tone across every cell, "
            "clean rounded grid dividers, premium cohesive layout, crisp "
            "legible typography."
        ),
    },
    # --- Events / Promo ---
    {
        "id": "event-editorial-minimal",
        "category": "events",
        "name": {"en": "Editorial Minimal", "bn": "এডিটোরিয়াল মিনিমাল"},
        "thumbnail": "/ai-presets/event1.webp",
        "prompt": (
            "A premium minimal promo graphic for {subject}: a warm cream/"
            "off-white background with generous negative space, one small "
            "elegant product photo in the lower third, and a large refined "
            "modern sans-serif headline in near-black text across the upper "
            "two-thirds — the headline and a short thin-outlined pill button "
            "beneath it should genuinely fit this specific business (use the "
            "real business context given above). Sophisticated boutique "
            "editorial aesthetic — no gradients, no starbursts, no neon. "
            "Every letter crisp, correctly spelled, legible. 1:1, this is the "
            "finished graphic."
        ),
    },
    {
        "id": "event-duotone-drop",
        "category": "events",
        "name": {"en": "Duotone Drop", "bn": "ডুওটোন ড্রপ"},
        "thumbnail": "/ai-presets/event2.webp",
        "prompt": (
            "A bold modern promo graphic for {subject} styled like a "
            "streetwear product-drop announcement: the product photo treated "
            "in a single striking duotone color filter filling the left half "
            "of the frame, a solid matching dark color block on the right "
            "half containing large condensed bold headline text and a sharp "
            "rectangular CTA button — copy should genuinely fit this specific "
            "business (use the real business context given above). High "
            "contrast, confident modern energy, no soft gradients. Every "
            "letter crisp, correctly spelled, legible. 1:1, this is the "
            "finished graphic."
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
            "light border, subtle shadow) floating centered over it, "
            "containing a bold headline and a solid rounded CTA button — copy "
            "should genuinely fit this specific business (use the real "
            "business context given above). Trendy modern app-UI aesthetic, "
            "soft ambient lighting behind the glass card. Every letter crisp, "
            "correctly spelled, legible. 1:1, this is the finished graphic."
        ),
    },
    {
        "id": "event-bold-type-poster",
        "category": "events",
        "name": {"en": "Bold Type Poster", "bn": "বোল্ড টাইপ পোস্টার"},
        "thumbnail": "/ai-presets/event4.webp",
        "prompt": (
            "A high-impact typographic promo poster for {subject}: massive "
            "oversized bold headline text fills most of the frame edge to "
            "edge as the dominant visual element — copy should genuinely fit "
            "this specific business (use the real business context given "
            "above) — with the product photo integrated behind or within the "
            "negative space of the lettering, two-tone black-and-white or "
            "single-accent-color palette, a small solid CTA button tucked in "
            "the bottom corner. Editorial fashion-poster energy, confident "
            "and graphic. Every letter crisp, correctly spelled, legible. "
            "1:1, this is the finished graphic."
        ),
    },
    {
        "id": "event-color-block-split",
        "category": "events",
        "name": {"en": "Color Block Split", "bn": "কালার ব্লক স্প্লিট"},
        "thumbnail": "/ai-presets/event5.webp",
        "prompt": (
            "A modern flat-design promo graphic for {subject}: the frame "
            "split cleanly into two solid color blocks (a confident two-tone "
            "palette, no gradients, no photo treatment), the product photo "
            "cut out cleanly on one side, a large bold headline and a solid "
            "rectangular CTA button on the other side — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above). Flat, graphic, poster-like confidence, "
            "sharp clean edges between the color blocks. Every letter crisp, "
            "correctly spelled, legible. 1:1, this is the finished graphic."
        ),
    },
    {
        "id": "event-framed-spotlight",
        "category": "events",
        "name": {"en": "Framed Spotlight", "bn": "ফ্রেমড স্পটলাইট"},
        "thumbnail": "/ai-presets/event6.webp",
        "prompt": (
            "A premium promo graphic for {subject}: the product photo sits "
            "centered within a thin rounded rectangular frame/border (like a "
            "museum label or gallery spotlight), soft focused lighting on "
            "the product inside the frame, with a refined headline and a "
            "small outlined CTA button placed neatly below the frame, "
            "outside it — copy should genuinely fit this specific business "
            "(use the real business context given above). Clean neutral "
            "background outside the frame, elegant gallery-retail "
            "aesthetic, no clutter. Every letter crisp, correctly spelled, "
            "legible. 1:1, this is the finished graphic."
        ),
    },
    # --- Marketing / Social ---
    {
        "id": "marketing-minimal-drop",
        "category": "marketing",
        "name": {"en": "Minimal Drop", "bn": "মিনিমাল ড্রপ"},
        "thumbnail": "/ai-presets/social1.webp",
        "prompt": (
            "A premium minimal product-drop social post for {subject}: a "
            "single product centered against a soft solid pastel background "
            "with generous negative space, a small confident caption in "
            "modern sans-serif text in the lower corner — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above). Understated branding-forward aesthetic — "
            "no gradients, no bursts, no clutter. Every letter crisp, "
            "correctly spelled, legible. 1:1, this is the finished, "
            "ready-to-post graphic."
        ),
    },
    {
        "id": "marketing-duotone-story",
        "category": "marketing",
        "name": {"en": "Duotone Story", "bn": "ডুওটোন স্টোরি"},
        "thumbnail": "/ai-presets/social2.webp",
        "prompt": (
            "A bold vertical 9:16 Instagram/Facebook Story graphic for "
            "{subject}: the product photo treated in a single striking "
            "duotone color filter filling most of the frame, with a large "
            "confident statement headline in bold modern type in the lower "
            "third over a solid-color strip for legibility — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above). High-contrast editorial-fashion energy — "
            "no soft gradients, no clip-art icons. Every letter crisp, "
            "correctly spelled, legible. This is the finished, ready-to-post "
            "graphic."
        ),
    },
    {
        "id": "marketing-collage-grid",
        "category": "marketing",
        "name": {"en": "Collage Grid", "bn": "কোলাজ গ্রিড"},
        "thumbnail": "/ai-presets/social3.webp",
        "prompt": (
            "A modern collage-style social post for {subject}: three to four "
            "photos of the product from different angles or contexts "
            "arranged in an organic overlapping collage (not a rigid grid), "
            "with one bold pull-quote style headline in large type "
            "overlapping the collage at a confident angle — copy should "
            "genuinely fit this specific business (use the real business "
            "context given above). Cohesive color grading across all photos, "
            "clean modern editorial feel. Every letter crisp, correctly "
            "spelled, legible. 1:1, this is the finished, ready-to-post "
            "graphic."
        ),
    },
    {
        "id": "marketing-glass-caption",
        "category": "marketing",
        "name": {"en": "Glass Caption", "bn": "গ্লাস ক্যাপশন"},
        "thumbnail": "/ai-presets/social4.webp",
        "prompt": (
            "A modern social post for {subject}: a full-bleed lifestyle "
            "photo of the product fills the entire square frame, with a "
            "frosted glassmorphism caption bar (translucent white, blurred "
            "backdrop, thin light border) across the bottom third, "
            "containing a short bold headline in clean modern type — copy "
            "should genuinely fit this specific business (use the real "
            "business context given above). Trendy modern app-UI aesthetic, "
            "soft ambient lighting. Every letter crisp, correctly spelled, "
            "legible. 1:1, this is the finished, ready-to-post graphic."
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
