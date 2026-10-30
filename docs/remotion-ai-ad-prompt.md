# Prompt: SoftuneBD AI video ad (Remotion)

Give everything below the line to the agent that works in the Remotion project.
Note for us: there is no Remotion project inside this repo yet, so the prompt tells the
agent to reuse the existing one and to copy assets from `landing/public/`.

---

## Task

Create a new Remotion composition, `AiAd30`, in our existing Remotion project: a **30 second,
vertical 1080x1920 (9:16), 30 fps** video ad for **SoftuneBD**, showing the platform's AI
visually with motion graphics and screen-style demos. Also export a 1:1 (1080x1080) and a
16:9 (1920x1080) variant by re-using the same scenes with a `layout` prop. Reuse the
project's existing components, fonts and helpers wherever they exist; do not rebuild what is
already there.

## Who it is for, and the one idea

Viewers are mostly **new or small business owners in Bangladesh**: people thinking about
starting a business, or already selling on Facebook with no website who find it hard to
manage. They are curious about AI and want something easy.

One idea: **"Tell the AI what you want. Your online business builds itself."**
Tone: simple, warm, confident, fast. No jargon on screen. Short words, big type.

## Language

Primary version in **Bangla** (on-screen text and captions). Also render an **English**
version from the same code via a `lang: "bn" | "en"` prop. Keep all copy in one `copy.ts`
object so both languages and later edits are easy. Bangla needs a proper font
(Hind Siliguri or Noto Sans Bengali, loaded with `@remotion/google-fonts`); English headings
use Plus Jakarta Sans 800. Always use burned-in captions, since most people watch muted.

## Brand

- Primary orange `#ff5a36`, soft orange `#fff0ec`, ink `#0f172a`, white. Dark scenes `#0b0f1a`.
- Rounded corners (24-32px), soft shadows, generous spacing, one accent colour per scene.
- Logo: `landing/public/logo-white.png` on dark, `landing/public/logo-dark.png` on light.
- Use the demo store **"Veila"** (women's fashion) for every example. Never show a real
  merchant's name, phone number or order.

## Storyboard (30 s)

All text below is on-screen text (Bangla version first, English in brackets). Voice-over is
optional; if used, read the same lines.

1. **0-3 s, hook.** Dark background, big text types in:
   "ব্যবসা শুরু করতে চান? শুধু বলুন।" ["Want to start a business? Just say it."]
   A chat input bar slides up with a blinking cursor.
2. **3-9 s, AI builds the store.** The input types: "আমার জন্য একটি ফ্যাশন স্টোর বানাও".
   Then a progress animation, and the Veila storefront assembles piece by piece
   (header, hero, products, categories sliding in with springs). Label chip: "AI স্টোর সেটআপ"
   ["AI store setup"].
3. **9-15 s, AI agent edits by chat.** Split view: left, a chat panel; right, the live store.
   Three quick requests, each changing the store instantly: "হোমপেজের লেখা বদলাও" (headline
   changes), "নতুন রং দাও" (colours shift to a new palette), "একটি প্রোডাক্ট যোগ করো" (a
   product card pops in). A small "Undo" button pulses at the end: "যেকোনো সময় ফেরান"
   ["Undo anytime"]. Label chip: "AI এজেন্ট" ["AI agent"].
4. **15-21 s, AI images.** A plain product photo + prompt text "সুন্দর হিরো ব্যানার বানাও".
   Show 3 image styles flipping through (a banner, a product shot, a category tile) with a
   shimmer reveal, then the best one drops into the store hero with a satisfying snap.
   Label chip: "AI ইমেজ" ["AI images"].
5. **21-26 s, ChatGPT connector.** A ChatGPT-style chat window (generic, do not copy
   OpenAI's logo or exact UI; use a neutral chat bubble style and the text "ChatGPT" in
   plain type). User: "আজকের সেলস কত?" The answer shows a small sales chart count-up.
   Then "হিরো ইমেজ বসাও" and the store hero updates live. Caption: "ChatGPT থেকেই
   আপনার স্টোর চালান" ["Run your store from ChatGPT"]. Add a small "বিটা" ["Beta"] tag.
6. **26-30 s, offer and CTA.** Everything zooms out to the finished store on a phone and a
   laptop. Text: "মাসে মাত্র ৳৩৯৯ থেকে" ["From just ৳399/month"] with a small "বার্ষিক বিলে"
   ["billed yearly"] note, then "৭ দিন ফ্রি, কার্ড লাগে না" ["7 days free, no card"], the logo,
   and `softunebd.com` in a large orange pill button that pulses. Last line: "এখনই শুরু করুন,
   বাকিটা আমরা দেখছি" ["Start now, we handle the rest"].

## Motion style

- Spring-based easing (`spring()` with low bounce), staggered entrances (3-6 frame delays),
  text revealed by mask or word-by-word, never plain fades only.
- Use `<Sequence>` per scene with 8-12 frame overlaps and cross-scene transitions
  (`@remotion/transitions`: slide, wipe, or a custom orange sweep). Keep one consistent set.
- Camera feel: slow scale (1.0 to 1.06) and small parallax on screens, 3D tilt on the phone.
- Cursor and click effects: animated cursor, ripple on click, typed text with a caret.
- Numbers count up (`interpolate`). Add a subtle grain or gradient glow, not busy backgrounds.
- Safe areas: keep text inside the middle 80% (leave top 250px and bottom 350px free for
  platform UI on Reels/Shorts/TikTok). Minimum text size 56px on 1080 wide. Max 6 words per line.
- Captions: bottom-centre, white on a dark rounded pill, word highlight in orange.

## Assets

Prefer real screenshots. Copy these from the SoftuneBD repo into the Remotion `public/`
folder; where a file is missing, **generate a placeholder PNG** (1080x1920 unless noted)
with a flat colour, the file name and a short description in large text, saved with the exact
name below, so we can replace it later with a ChatGPT-made image without touching code.
Load every image through one `assets.ts` map so swapping is a one-line change.

| Use | Path in Remotion `public/` | Source / placeholder |
|---|---|---|
| Logo light and dark | `logo-white.png`, `logo-dark.png` | copy from `landing/public/` |
| Dashboard on desktop | `dashboard-d.webp` | copy from `landing/public/dashboard-d.webp` |
| AI assistant screen | `ai-d.webp` | copy from `landing/public/showcase/ai-d.webp` |
| AI chat screen | `chat-d.webp` | copy from `landing/public/showcase/chat-d.webp` |
| Theme editor screen | `editor-d.webp` | copy from `landing/public/showcase/editor-d.webp` |
| Veila storefront, full page (desktop) | `veila-desktop.png` | **placeholder**, 1600x1000 |
| Veila storefront on phone | `veila-mobile.png` | **placeholder**, 750x1500 |
| Veila hero banner (final) | `veila-hero.png` | **placeholder**, 1672x941 |
| 3 AI image styles: banner, product, category tile | `ai-style-1.png`, `ai-style-2.png`, `ai-style-3.png` | **placeholders**, 1080x1080 |
| Plain product photo (before) | `product-before.png` | **placeholder**, 1080x1080 |
| ChatGPT-style chat window (neutral) | build in code, no image | motion graphic |
| Two or three product cards | `product-1.png` to `product-3.png` | **placeholders**, 800x800 |
| Background music | `music.mp3` | **placeholder**: silent file; note where it goes |

Write a small script `scripts/make-placeholders.mjs` that creates every placeholder above
(use `sharp` or the canvas API), so missing images never block a render.

## Build requirements

- One root composition per size, all driven by the same scenes; props: `lang`, `layout`.
- Scenes as separate components: `Hook`, `StoreBuild`, `AgentChat`, `AiImages`,
  `ChatGptConnector`, `Cta`. Timings (in frames at 30 fps) live in one constants file.
- All copy in `copy.ts` (bn and en). All colours and sizes in `theme.ts`.
- No external network calls at render time; fonts via `@remotion/google-fonts`.
- Add npm scripts: `render:bn` (9:16 Bangla), `render:en` (9:16 English), `render:square`,
  `render:wide`, each writing to `out/`.
- Then run `npx remotion render` for the 9:16 Bangla version and report the output path, the
  file size, and any placeholder that is still being used.

## Rules (do not break)

- Do not claim anything we cannot show: no fake numbers of users or sales, no "official
  ChatGPT app" wording, no OpenAI logo. Say "ChatGPT connector (beta)".
- No real customer data, no real phone numbers, no payment details on screen.
- Keep the price exactly: **৳399/month when billed yearly (Starter)**. Do not show other
  plan prices in this ad.
- Do not edit the SoftuneBD app code. Only add to the Remotion project (and copy assets).

## Done when

A 30 s 9:16 Bangla render plays smoothly, every on-screen word is readable muted, the last
frame holds the CTA for at least 1.5 s, all four size/language renders work from the scripts,
and the list of placeholder images to replace is printed at the end.
