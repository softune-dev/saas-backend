---
name: add-block-type
description: Add a new editable section type (block) to the site editor — e.g. a video embed, team grid, opening-hours table, map, or newsletter signup. Use when the user wants a new kind of content section that customers can add to their pages, or mentions adding to the block registry.
---

# Add a block type

Adding an editable section is a **one-file backend change**. No migration, no new
endpoint, no model. That is the whole point of the registry design.

## Steps

### 1. Add the entry to `REGISTRY` in `app/blocks.py`

Use the `f()` helper. Keep it alphabetically near similar blocks.

```python
"VideoEmbed": {
    "label": "Video",
    "description": "Embedded video with an optional caption.",
    "fields": [
        f("heading", "text", "Heading", max_len=120),
        f("provider", "select", "Provider", default="youtube",
          options=["youtube", "vimeo"]),
        f("video_id", "text", "Video ID", required=True,
          help_="Just the id, not the full URL."),
        f("caption", "textarea", "Caption", max_len=300),
    ],
},
```

### 2. Field type reference

| Type | Admin panel widget | Stored as |
|---|---|---|
| `text` | single-line input | string |
| `textarea` | multi-line | string |
| `image` | image picker (upload → URL) | string (public URL) |
| `url` | url input | string |
| `number` | number input | int |
| `boolean` | switch | bool |
| `select` | dropdown — **requires `options`** | string |
| `list` | repeatable rows — **requires `fields`** | list of dicts |

### 3. Rules the consistency tests enforce

- A `select` must have `options`; a `list` must have `fields`.
- A `required=True` field must NOT have a `default` — a default means it can never
  fail validation, making the required flag a lie the UI renders as an asterisk.
- Set `max_len` on every free-text field. Unbounded text in a hero heading breaks
  layouts and there is no database-level length check on JSONB.

### 4. Let templates use it

A block type nobody declares is invisible in the admin panel. Add it to the
template's manifest:

```sql
UPDATE templates
SET block_types = array_append(block_types, 'VideoEmbed')
WHERE key = 'restaurant-01';
```

Note: `tests/test_registry_consistency.py` asserts that every type in
`block_types` exists in the registry, and that seeded `default_pages` only use
declared types. Run `pytest tests/test_registry_consistency.py` after.

### 5. Verify

```bash
pytest tests/test_registry_consistency.py -v
```

Then restart the server and check `GET /blocks` — the new type should appear with
its full field spec. The admin panel needs no change; it generates the form.

## Do NOT

- Add a database column or migration for block content. It lives in
  `site_pages.blocks` JSONB.
- Add a new endpoint. `PATCH /sites/{id}/pages/{pid}` already handles it.
- Skip `max_len` on text fields.
- Change an existing block's field *names* without checking who uses them:
  ```sql
  SELECT s.subdomain, p.slug FROM site_pages p
  JOIN sites s ON s.id = p.site_id
  WHERE p.blocks @> '[{"type": "Hero"}]';
  ```
  Renaming a field silently drops that data on the next save, because
  `_validate_fields` strips unknown keys. Migrate the JSONB first, or add the new
  field alongside the old one.

## Finally

Tell the user the template repo on Vercel still needs a React component for the
new type — the backend now accepts and serves it, but nothing renders it yet.
