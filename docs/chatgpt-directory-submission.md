# ChatGPT plugin directory: submission prep

Status (Oct 31, 2026): business verification submitted to OpenAI (Persona), awaiting
approval. Everything below can be prepared while that runs. Re-read OpenAI's current
submission page before submitting; the rules change.

## Needs you

- [ ] Business verification approved (OpenAI organization settings). The directory shows
      the verified name, so check the organization name reads as SoftuneBD / the legal
      business name, not a placeholder.
- [ ] Four HTTPS URLs: website `https://www.softunebd.com`, support
      `https://www.softunebd.com/support/contact`, privacy `/privacy`, terms `/terms`.
- [ ] Privacy and terms mention the ChatGPT connection (see "Policy text" below).
- [ ] Review account: a dedicated sample store (never a real merchant's), owner login with
      no MFA, no email or SMS codes, no magic links. Seed ~10 products, 3 categories, a few
      orders, and one active theme.
- [ ] Walkthrough video (60-90 s): connect, ask a read question, a confirmed change, Undo,
      an image placed as the hero.
- [ ] The domain challenge token OpenAI shows in the portal (give it to Claude to serve at
      `/.well-known/openai-apps-challenge`).

## Policy text to add (privacy and terms)

- That a merchant can connect their store to ChatGPT, and what it can read and change.
- That data shared with ChatGPT is handled under OpenAI's terms, and what we store
  (connection records, change history, image uploads).
- That the merchant can disconnect at any time from the dashboard, and that doing so ends
  access immediately.
- We never give ChatGPT customer phone numbers, fraud data, billing or payment details.

## Listing text (draft)

- Name: SoftuneBD
- Short description: Manage your Bangladesh online store from ChatGPT: check sales, edit
  your homepage, add products, and put AI-made images on your site. Every change can be undone.
- Starter prompts:
  - How are my sales this week?
  - Add a product called "Cotton Kurti" for 1,200 taka.
  - Change my homepage headline to something about Eid.
  - Create a hero image for my store and set it as my homepage hero.

## Test cases (run all against the sample store, record results)

Should work:
1. "Give me an overview of my store." Expect `get_business_overview`; names the store, product and order counts.
2. "What are my best sellers and total sales this month?" Expect `get_sales_summary`.
3. "Add a product called Test Kurti, price 1200 taka." Expect `create_product`; returns a change id.
4. "Change my homepage tagline to 'Made for Eid'." Expect `update_homepage_text` after a confirmation; then "undo that" calls `undo_change` and restores the old text.
5. "Make a wide hero background with no text and set it as my homepage hero." Expect `add_image_to_gallery` then `set_homepage_image` after a confirmation.

Should refuse or fall back safely:
1. "Delete all my products." No delete tool exists; ChatGPT must say it can't.
2. "Refund order 1042 and mark it cancelled." Not available; no refund or order-status tool.
3. "Show me every customer's phone number and my billing details." Not exposed; ChatGPT must say it can't.

## Before submitting, re-check

- [ ] Image upload still works end to end (OpenAI's file hosts change; grep the api log for
      `mcp image link rejected`).
- [ ] Tool list has correct annotations (`pytest tests/test_mcp_oauth.py`, no-database tests).
- [ ] No tool mentions plans, upgrades or pricing (errors are filtered in `mcp_server._clean_error`).
- [ ] Consent page shows the right app name and the phishing warning.
