"""Uploads demo-assets/<slug>/ to Cloudinary and writes urls/<slug>.json.

Run locally (it only needs the Cloudinary keys from .env, no database):

    venv\\Scripts\\python.exe scripts\\demo_stores\\upload_assets.py voltix noor ...

Each PNG becomes a WebP (much smaller, same look; the logo keeps its
transparency). Desktop heroes are centre-cropped from 3:2 to 16:9, the
shape the themes show. Files go to softune/demo/<slug>/ with fixed names,
so re-running replaces them instead of piling up copies.
"""

import io
import json
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(__file__))

import cloudinary  # noqa: E402
import cloudinary.uploader  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import settings  # noqa: E402
from catalog import STORES, image_list  # noqa: E402

URLS = os.path.join(os.path.dirname(__file__), "urls")


def prepare(path: str, kind: str) -> bytes:
    im = Image.open(path)
    im = im.convert("RGBA") if kind == "logo" else im.convert("RGB")
    if kind == "hero":
        w, h = im.size
        target_h = round(w * 9 / 16)
        top = (h - target_h) // 2
        im = im.crop((0, top, w, top + target_h))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=88, method=6)
    return buf.getvalue()


def main() -> None:
    slugs = sys.argv[1:]
    if not slugs:
        raise SystemExit("usage: upload_assets.py <slug> [<slug> ...]")
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    os.makedirs(URLS, exist_ok=True)
    by_slug = {s["slug"]: s for s in STORES}
    for slug in slugs:
        store = by_slug[slug]
        out = {}
        for fname, kind, _ in image_list(store):
            path = os.path.join("demo-assets", slug, fname)
            if not os.path.exists(path):
                raise SystemExit(f"{slug}: missing {fname}, run check_assets.py first")
            public_id = f"softune/demo/{slug}/{os.path.splitext(fname)[0]}"
            res = cloudinary.uploader.upload(
                prepare(path, kind), public_id=public_id, overwrite=True,
                resource_type="image", format="webp",
            )
            out[fname] = res["secure_url"]
            print(f"  {slug}/{fname}")
        with open(os.path.join(URLS, f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"{slug}: {len(out)} images uploaded")


if __name__ == "__main__":
    main()
