"""Writes docs/demo-stores/<slug>.md: the ChatGPT image prompts for each demo
store, generated from catalog.py so filenames always match what seed.py reads.

    venv\Scripts\python.exe scripts/demo_stores/make_prompts.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from catalog import SIZES, STORES, image_list  # noqa: E402

OUT = os.path.join("docs", "demo-stores")
THEME_NAME = {"aurora": "Theme 1 (editorial)", "bazaar": "Theme 2 (marketplace)", "fashion-classic": "Theme 3 (boutique)"}


def store_doc(s: dict) -> str:
    images = image_list(s)
    lines = [
        f"# {s['name']}: image prompts",
        "",
        f"{THEME_NAME[s['theme']]} · {s['niche']} · {len(images)} images",
        f"Save every image into `demo-assets/{s['slug']}/` with the exact filename shown.",
        "",
        "## 1. Paste this first (sets the style for the whole chat)",
        "",
        "```",
        f"You are the art director for \"{s['name']}\", an online store in Bangladesh selling {s['niche'].lower()}.",
        f"Tagline: \"{s['tagline']}\".",
        f"Brand colours: primary {s['colors']['primary']}, accent {s['colors']['accent']}, background {s['colors']['surface']}.",
        f"Visual style for EVERY image: {s['style']}",
        "Rules for every image: photorealistic and high-end, consistent lighting and colour across the whole set,",
        "no watermarks, no real brand logos, no extra text unless I ask for it, and any text you add must be spelled exactly as written.",
        "I will ask for one image at a time and tell you its size. Reply with just the image.",
        "```",
        "",
        "## 2. Then generate these one by one",
        "",
    ]
    for n, (fname, kind, prompt) in enumerate(images, 1):
        w, h, what = SIZES[kind]
        lines += [
            f"### {n}. `{fname}`  ({w}x{h}, {what})",
            "",
            "```",
            f"Size {w}x{h}. {prompt}",
            "```",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    total = 0
    index = ["# Demo store image prompts", "",
             "10 demo stores built on SoftuneBD's 3 themes. For each store: open a new ChatGPT chat,",
             "paste section 1 of its file, then ask for each image in section 2 and save it with the exact",
             "filename into `demo-assets/<store>/` (that folder is not in git).",
             "",
             "When a store's folder is complete, check it:",
             "",
             "```",
             "venv\Scripts\python.exe scripts\demo_stores\check_assets.py",
             "```",
             "",
             "| Store | Theme | Niche | Images | Prompts |",
             "|---|---|---|---|---|"]
    for s in STORES:
        doc = store_doc(s)
        n = len(image_list(s))
        total += n
        with open(os.path.join(OUT, f"{s['slug']}.md"), "w", encoding="utf-8") as f:
            f.write(doc)
        index.append(f"| {s['name']} | {THEME_NAME[s['theme']]} | {s['niche']} | {n} | [{s['slug']}.md]({s['slug']}.md) |")
    index += ["", f"Total: {total} images."]
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index) + "\n")
    for s in STORES:
        os.makedirs(os.path.join("demo-assets", s["slug"]), exist_ok=True)
    print(f"wrote {len(STORES)} prompt files, {total} images in total")


if __name__ == "__main__":
    main()
