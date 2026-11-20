"""Checks demo-assets/<slug>/ against catalog.py: missing files, and images
whose size doesn't match what was asked (wrong shape would crop badly).

    venv\Scripts\python.exe scripts/demo_stores/check_assets.py [slug]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from catalog import SIZES, STORES, image_list  # noqa: E402
from PIL import Image  # noqa: E402


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    all_ok = True
    for s in STORES:
        if only and s["slug"] != only:
            continue
        folder = os.path.join("demo-assets", s["slug"])
        missing, wrong = [], []
        for fname, kind, _ in image_list(s):
            path = os.path.join(folder, fname)
            if not os.path.exists(path):
                missing.append(fname)
                continue
            w, h, _ = SIZES[kind]
            with Image.open(path) as im:
                ratio, want = im.width / im.height, w / h
                if abs(ratio - want) > 0.03:
                    wrong.append(f"{fname} is {im.width}x{im.height}, want {w}x{h}")
        total = len(image_list(s))
        status = "ready" if not missing and not wrong else f"{total - len(missing)}/{total}"
        print(f"{s['slug']:10s} {status}")
        for m in missing:
            print(f"   missing  {m}")
        for w_ in wrong:
            print(f"   shape    {w_}")
        all_ok = all_ok and not missing and not wrong
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
