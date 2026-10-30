"""LIVE agent evaluation against real merchant conversations (Oct 2026).

Not collected by a normal `pytest` run (the file name doesn't start with
test_) because it calls the real Gemini API and costs a little (~$0.30).
Run it on purpose:

    pytest tests/eval_agent_live.py -s

Each scenario replays what a real merchant typed (from ai_usage_logs) on a
fresh throwaway store, then checks the thing that went wrong before: the old
assistant answered "go to Theme Editor and paste this" where it should have
done the work. A scenario passes when the agent used the expected write tool
(or, where no tool exists, answered honestly without pretending). A summary
is printed and saved to eval_agent_report.json for reading.
"""

import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.asyncio

# (name, [merchant messages in order], tools that must appear in the whole
# conversation (any one of them), or None = no write expected)
SCENARIOS = [
    ("theme colors, then 'you do it'", [
        "আমি আমার ওয়েবসাইটের থিমের কালার পরিবর্তন করতে চাই। Premium Luxury Black & Golden Theme চাই — "
        "ব্যাকগ্রাউন্ড কালো, হেডিং গোল্ডেন, লেখা সাদা, বাটন গোল্ডেন।",
        "আপনি করে দেন",
    ], {"update_storefront_design"}),
    ("why-choose-us copy, then 'add it'", [
        "Why choose us",
        "Please add this in why us section",
    ], {"update_homepage_text"}),
    ("banner title/body/button", [
        "banner এ আছে tittle, body, button, আমি shop now দিলে tittle, body, button কি হবে",
        "আরো আকর্ষণীয় কিছু দাও এবং এটা বসিয়ে দাও",
    ], {"update_homepage_text"}),
    ("add a product", [
        "I want to add a new product.",
        "Red cotton kurti, 1250 taka, 10 in stock",
    ], {"create_product"}),
    ("popup discount", [
        "পপআপ তৈরির সুবিধা আছে কি না",
        "হ্যাঁ, ঈদ উপলক্ষে ২০% ছাড়ের একটা পপআপ বানিয়ে দাও",
    ], {"create_discount_event"}),
    ("15 categories pasted", [
        "New Arrivals\nTrending Products\nMen's Fashion\nWomen's Fashion\nElectronics & Gadgets\n"
        "Beauty & Personal Care\nHome & Living\nWatches & Accessories\nBags & Wallets\nShoes & Footwear",
    ], {"propose_replace_all_categories", "add_category"}),
    ("photos into categories (no tool yet)", [
        "আমি আপনাকে ফটো দিলে আপনি কি আমাকে ফটোগুলো এড করে দিতে পারবেন ক্যাটাগরি গুলোর ভিতরে",
    ], None),
]

# Signs of the old failure: directions to go do it yourself.
_INSTRUCTIONS = re.compile(
    r"(theme editor|থিম এডিটর|customize|go to|যান\b|ড্যাশবোর্ড থেকে|follow these steps|নিচের ধাপ)",
    re.IGNORECASE,
)


async def _turn(account, message, history):
    response = await account.post("/ai/agent/chat", json={"message": message, "history": history})
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    tools = next((e["tools_used"] for e in events if e["type"] == "done"), [])
    errors = [e["message"] for e in events if e["type"] == "error"]
    return text, tools, errors


async def test_real_conversations(account, site):
    report, failures = [], []
    for name, messages, expected in SCENARIOS:
        history: list[dict] = []
        all_tools: list[str] = []
        turns = []
        for message in messages:
            text, tools, errors = await _turn(account, message, history)
            all_tools += tools
            turns.append({"merchant": message, "agent": text, "tools": tools, "errors": errors})
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": text}]
        did_write = bool(expected and expected & set(all_tools))
        told_to_do_it_yourself = bool(_INSTRUCTIONS.search(turns[-1]["agent"])) and not did_write
        ok = (did_write if expected else True) and not told_to_do_it_yourself and not any(t["errors"] for t in turns)
        report.append({"scenario": name, "pass": ok, "tools": all_tools, "turns": turns})
        print(f"\n{'PASS' if ok else 'FAIL'}  {name}  tools={all_tools}")
        for t in turns:
            print(f"   merchant: {t['merchant'][:90]}")
            print(f"   agent:    {t['agent'][:260].replace(chr(10), ' / ')}")
        if not ok:
            failures.append(name)

    pathlib.Path("eval_agent_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    assert not failures, f"{len(failures)}/{len(SCENARIOS)} scenarios failed: {failures}"
