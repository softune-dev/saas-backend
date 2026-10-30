# ChatGPT plugin (remote MCP server)

Lets a merchant manage their SoftuneBD store from ChatGPT. ChatGPT does the
reasoning on the merchant's own ChatGPT plan; our API only runs the tools. So
there is no Gemini cost, but each tool call still uses our database and server.

## How it fits together

| Piece | Where | What it does |
|---|---|---|
| MCP server | `POST /mcp` (`app/mcp_server.py`, `app/api/mcp.py`) | Answers ChatGPT's tool calls |
| OAuth server | `/oauth/*`, `/.well-known/*` (`app/oauth_server.py`, `app/api/oauth.py`) | Logs a merchant in for ChatGPT (OAuth 2.1 + PKCE) |
| Approval page | dashboard `/connect/chatgpt` | The merchant approves, and can disconnect |
| Tables | `migrations/080_oauth_mcp.sql` | clients, one-time codes, tokens (hashes only) |

Flow: ChatGPT registers itself, sends the merchant to `/oauth/authorize`, which
redirects to the dashboard approval page; the merchant approves; ChatGPT swaps
the code for a one-hour access token plus a refresh token, then calls `/mcp`
with it. Every token is tied to one store.

## What the plugin can do (v1)

Read: store overview, products, orders (customer name only), sales summary,
categories, sale events, store settings, storefront design, recent changes.

Change, all reversible: store colors/fonts, homepage text, add category, add
or edit a product, create a sale event, undo a change.

Images: `add_image_to_gallery` saves an image from the chat (one ChatGPT made or
the merchant attached) into the media library; `set_homepage_image`,
`set_category_image` and `add_product_image` then place it (these have Undo).
ChatGPT passes the file as a temporary download link (`openai/fileParams`);
`app/mcp_images.py` only fetches it from `mcp_image_hosts` over https, with no
redirects and a 10MB cap, then applies the normal size and storage checks.

Deliberately not included: deleting, refunds, order status, replacing all
categories, customer phone numbers, fraud data, courier/payment details,
billing and plans. OpenAI's rules forbid showing plans or promoting upgrades,
and text a shopper typed could try to steer an AI into misusing a tool.

Tools that overwrite existing data are marked `destructiveHint: true`, even
though they can be undone (OpenAI: being undoable does not justify `false`),
so ChatGPT asks the merchant to confirm each one.

## Private pilot: turning it on

1. Run `migrations/080_oauth_mcp.sql` in the Supabase SQL Editor (Singapore).
2. Deploy the backend (`docker compose -f docker-compose.prod.yml up -d --build api worker`).
3. Deploy the dashboard (merge the PR that adds `/connect/chatgpt`).
4. Check discovery: `curl https://api.softunebd.com/.well-known/oauth-protected-resource`
   and `.../.well-known/oauth-authorization-server` both return JSON.
5. In ChatGPT: Plugins -> add a custom MCP server -> URL `https://api.softunebd.com/mcp`,
   authentication OAuth. Connect, approve on the dashboard page, then ask
   ChatGPT something like "how are my sales this month?".

## Signed out vs signed in

`initialize`, `ping` and `tools/list` work WITHOUT a token on purpose: ChatGPT
has to read the tool list and each tool's `securitySchemes` before it can offer
to link an account (OpenAI: the linking prompt needs both the per-tool
`securitySchemes` and a tool-level `_meta["mcp/www_authenticate"]` challenge).
The list contains no store data. `tools/call` without a token answers with that
challenge. A token that IS presented but is expired, revoked or wrong gets a
real HTTP 401. If ChatGPT shows "This app does not provide a browser setup URL",
delete the connector and add it again so it re-reads the tools.

## Limits

- 60 tool calls per minute and 60 changes per hour per store (`mcp_calls_per_minute`,
  `mcp_writes_per_hour` in `app/config.py`). Needs Redis; fails open if it is down.
- Suspended, overdue and expired-trial stores, and the shared demo account, cannot connect.
- Access tokens last 60 minutes, refresh tokens 30 days (rotated on every use).

## Before submitting to OpenAI's directory

Verified OpenAI developer identity; four HTTPS URLs (website, support, privacy,
terms); the verification token served at
`https://<host>/.well-known/openai-apps-challenge`; five "should work" and three
"should refuse" test cases; a demo video; a reviewer account without MFA/email
codes (create a dedicated sample store, never use real data). Re-check the
guidelines then, they change.

## Not verified yet

- The database-backed tests in `tests/test_mcp_oauth.py` need migration 080 on the
  database `DATABASE_URL` points at.
- Verified in real ChatGPT (Oct 2026, store Veila): sign-in, store overview read, and a
  homepage-tagline write with ChatGPT's confirmation prompt and read-back, and Undo.
  The other write tools and other ChatGPT plans are not tested yet.
- Image upload verified live (Oct 2026): a ChatGPT-generated hero was added to the media
  library and set as the desktop hero. Needed (1) the full four-property file schema
  (`download_url`, `file_id`, `mime_type`, `file_name`) or ChatGPT passes file ids
  instead of links, and (2) allowing OpenAI's regional Azure hosts
  (`oaisdmntpr<region>.blob.core.windows.net`, by pattern, see `mcp_image_host_patterns`).
  If uploads break again, grep the api log for `mcp image link rejected` to see the host.
  The mobile hero slot (`hero_mobile`) is not tested live yet.
- Whether other MCP apps (Claude, etc.) work with this OAuth setup.
