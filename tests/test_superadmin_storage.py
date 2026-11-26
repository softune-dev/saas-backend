"""The superadmin Storage page. The folder arithmetic is tested on a made-up
listing, so no test calls Cloudinary."""

from datetime import UTC, datetime

from app import storage_report
from app.api.superadmin_storage import split_folders

MB = 1024 * 1024


def _file(public_id: str, size: int, kind: str = "image") -> dict:
    return {"public_id": public_id, "bytes": size, "resource_type": kind, "secure_url": "https://x/" + public_id}


LISTING = [
    _file("softune/shop-a/hero/one", 30 * MB),
    _file("softune/shop-a/products/two", 60 * MB, "video"),
    _file("softune/demo/raaj/hero", 5 * MB),
    _file("softune/gone-shop/other/x", 2 * MB),
    _file("sample", 1 * MB),
]


def test_summarise_counts_files_folders_and_kinds():
    out = storage_report.summarise(LISTING, "softune")
    assert out["folders"]["shop-a"] == {"files": 2, "bytes": 90 * MB, "images": 1, "videos": 1}
    assert out["kinds"]["video"] == {"files": 1, "bytes": 60 * MB}
    assert out["outside_root"] == {"files": 1, "bytes": MB}
    # shop-a, shop-a/hero, shop-a/products, demo, demo/raaj, gone-shop, gone-shop/other
    assert out["folder_paths"] == 7
    assert [i["bytes"] for i in out["largest"]][:2] == [60 * MB, 30 * MB]


def test_stores_are_measured_against_their_plan_and_leftovers_are_named():
    folders = storage_report.summarise(LISTING, "softune")["folders"]
    stores, others = split_folders(
        folders, ["shop-a", "demo", "gone-shop", "empty-old"],
        [("shop-a", "t1", "Shop A", "free"), ("quiet", "t2", "Quiet", "starter")],
    )
    assert [(s["folder"], s["pct"]) for s in stores] == [("shop-a", 90.0), ("quiet", 0.0)]
    kinds = {o["folder"]: o["kind"] for o in others}
    assert kinds == {"demo": "platform", "gone-shop": "unmatched", "empty-old": "unmatched"}


async def test_storage_is_hidden_from_merchants(account):
    assert (await account.get("/superadmin/storage")).status_code == 404


async def test_a_cloudinary_failure_keeps_the_last_good_copy(monkeypatch):
    good = {"folders": {}, "scanned_at": datetime.now(UTC)}
    monkeypatch.setattr(storage_report, "_cache", good)
    monkeypatch.setattr(storage_report, "_cached_at", -10_000.0)

    def broken():
        raise RuntimeError("cloudinary down")

    monkeypatch.setattr(storage_report, "_scan", broken)
    report, error = await storage_report.media_report(refresh=True)
    assert report is good and error
