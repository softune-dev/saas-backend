"""The merchant's list of questions the assistant couldn't answer."""

from app.assistant import service


async def _host(account, site) -> str:
    published = await account.post(f"/sites/{site['id']}/publish", json={})
    assert published.status_code == 200
    return published.json()["subdomain"]


async def _ask(account, host: str, question: str) -> dict:
    res = await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": question})
    assert res.status_code == 200, res.text
    return res.json()


async def _open(account, site) -> list[dict]:
    return (await account.get(f"/sites/{site['id']}/assistant/questions")).json()["items"]


async def test_unanswered_question_is_listed_and_repeats_are_counted_once(account, site):
    host = await _host(account, site)
    assert (await _ask(account, host, "Do you deliver to Sylhet?"))["kind"] == "fallback"
    # Same question, different wording and order: one row, counted twice.
    await _ask(account, host, "sylhet delivery do you?")

    items = await _open(account, site)
    assert len(items) == 1
    assert items[0]["times_asked"] == 2 and items[0]["status"] == "open"
    assert items[0]["question"] == "Do you deliver to Sylhet?", "keeps the first wording"


async def test_phone_numbers_are_masked_before_storage(account, site):
    host = await _host(account, site)
    await _ask(account, host, "can zorblax gadget be held for 01712 345678")
    stored = (await _open(account, site))[0]["question"]
    assert "345678" not in stored and "01712" not in stored


async def test_answered_questions_are_not_listed_and_the_assistant_uses_the_answer(account, site):
    host = await _host(account, site)
    await _ask(account, host, "Do you deliver to Sylhet?")
    question = (await _open(account, site))[0]

    res = await account.post(
        f"/sites/{site['id']}/assistant/questions/{question['id']}/answer",
        json={"answer": "Yes, Sylhet takes 3 working days."},
    )
    assert res.status_code == 200 and res.json()["status"] == "answered"
    assert await _open(account, site) == []

    # The shopper's next ask is answered from the merchant's words.
    reply = await _ask(account, host, "Do you deliver to Sylhet?")
    assert reply["kind"] == "custom" and "3 working days" in reply["answer"]
    # And it is now part of the merchant's saved settings.
    saved = (await account.get(f"/sites/{site['id']}")).json()["assistant"]
    assert saved["qa"][-1]["a"] == "Yes, Sylhet takes 3 working days."


async def test_dismiss_reopen_and_delete(account, site):
    host = await _host(account, site)
    await _ask(account, host, "Is the moon made of cheese?")
    q = (await _open(account, site))[0]
    url = f"/sites/{site['id']}/assistant/questions/{q['id']}"

    assert (await account.patch(url, json={"status": "dismissed"})).json()["status"] == "dismissed"
    assert await _open(account, site) == []
    assert (await account.patch(url, json={"status": "open"})).json()["status"] == "open"
    # 'answered' can only come from the answer endpoint.
    assert (await account.patch(url, json={"status": "answered"})).status_code == 422
    assert (await account.delete(url)).status_code == 204
    assert await _open(account, site) == []


async def test_a_site_stops_collecting_new_questions_at_the_cap(account, site, monkeypatch):
    monkeypatch.setattr(service, "MAX_OPEN_PER_SITE", 2)
    host = await _host(account, site)
    for q in ("alpha question one", "beta question two", "gamma question three"):
        await _ask(account, host, q)
    assert len(await _open(account, site)) == 2
    # A question already in the list still counts after the cap is reached.
    await _ask(account, host, "alpha question one")
    top = {i["question"]: i["times_asked"] for i in await _open(account, site)}
    assert top["alpha question one"] == 2


async def test_answer_is_refused_when_the_written_answers_are_full(account, site):
    full = [{"q": f"question number {i}", "a": "an answer"} for i in range(100)]
    assert (await account.patch(f"/sites/{site['id']}", json={"assistant": {"qa": full}})).status_code == 200
    host = await _host(account, site)
    await _ask(account, host, "something brand new here")
    q = (await _open(account, site))[0]
    res = await account.post(f"/sites/{site['id']}/assistant/questions/{q['id']}/answer", json={"answer": "x"})
    assert res.status_code == 409
