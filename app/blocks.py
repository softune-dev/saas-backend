"""THE BLOCK REGISTRY — the contract that makes one admin panel edit many sites.

This is the most important design file in the project. Read it before changing
anything about how sites are edited.

The problem it solves: every template needs a different set of sections, and
every section needs different editable fields. Hard-coding that means a database
migration and an admin-panel rewrite per template.

The solution: describe each block type ONCE, here, as data. Then

  * the admin panel GETs /blocks and generates its edit form from these specs —
    it never needs to know what a "Hero" is;
  * the API validates incoming block data against these same specs, so the
    JSONB column cannot fill up with malformed junk;
  * each template declares which block types it supports (templates.block_types),
    and a site's page is just an ordered array of blocks with values.

ONE source of truth, therefore no drift between what the form offers, what the
API accepts, and what the template renders.

TO ADD A NEW BLOCK TYPE: add an entry to REGISTRY below. That is the whole
backend change — no migration, no new endpoint, no new model. Then teach the
template repo to render it.
"""

from typing import Any, Literal

FieldType = Literal[
    "text",      # single-line string      -> <input type=text>
    "textarea",  # multi-line string       -> <textarea>
    "image",     # public URL of an upload -> image picker widget
    "url",       # link target             -> <input type=url>
    "number",    # integer                 -> <input type=number>
    "boolean",   # on/off                  -> checkbox / switch
    "select",    # one of `options`        -> <select>
    "list",      # repeatable group of `fields` -> add/remove rows
]


def f(
    name: str,
    type_: FieldType,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    help_: str = "",
    options: list[str] | None = None,
    fields: list[dict] | None = None,
    max_len: int | None = None,
) -> dict:
    """Build one field spec. Keeps REGISTRY below readable instead of a wall of dicts."""
    spec: dict[str, Any] = {
        "name": name, "type": type_, "label": label,
        "required": required, "default": default, "help": help_,
    }
    if options:
        spec["options"] = options
    if fields:
        spec["fields"] = fields
    if max_len:
        spec["max_len"] = max_len
    return spec


# --- reusable sub-field groups (used by `list` fields) ----------------------
_LINK = [f("label", "text", "Label", required=True), f("url", "url", "URL", required=True)]
_IMAGE_ITEM = [f("url", "image", "Image", required=True), f("alt", "text", "Alt text")]


REGISTRY: dict[str, dict] = {
    "Hero": {
        "label": "Hero",
        "description": "Big headline at the top of a page.",
        "fields": [
            f("heading", "text", "Headline", required=True, max_len=120),
            f("subheading", "textarea", "Sub-headline", max_len=300),
            f("image_url", "image", "Background image"),
            f("cta_text", "text", "Button text", max_len=40),
            f("cta_link", "url", "Button link"),
            f("align", "select", "Alignment", default="center",
              options=["left", "center", "right"]),
        ],
    },
    "About": {
        "label": "About / Story",
        "description": "Text block with an optional image beside it.",
        "fields": [
            f("heading", "text", "Heading", required=True, max_len=120),
            f("body", "textarea", "Body text", required=True, max_len=2000),
            f("image_url", "image", "Image"),
        ],
    },
    "FeatureGrid": {
        "label": "Feature grid",
        "description": "Grid of short selling points.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("columns", "number", "Columns", default=3,
              help_="2, 3 or 4 looks best."),
            f("items", "list", "Features", fields=[
                f("icon", "text", "Icon name", help_="lucide.dev icon name, e.g. 'zap'"),
                f("title", "text", "Title", required=True, max_len=60),
                f("body", "textarea", "Description", max_len=240),
            ]),
        ],
    },
    "MenuList": {
        "label": "Menu / price list",
        "description": "Named items with prices. Good for restaurants and services.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("items", "list", "Items", fields=[
                f("name", "text", "Name", required=True, max_len=80),
                f("description", "text", "Description", max_len=200),
                f("price_cents", "number", "Price (in cents)", required=True,
                  help_="1450 means 14.50 — always cents, never decimals."),
            ]),
        ],
    },
    "Gallery": {
        "label": "Image gallery",
        "description": "Grid of images.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("columns", "number", "Columns", default=3),
            f("images", "list", "Images", fields=_IMAGE_ITEM),
        ],
    },
    "Testimonials": {
        "label": "Testimonials",
        "description": "Customer quotes.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("items", "list", "Quotes", fields=[
                f("quote", "textarea", "Quote", required=True, max_len=400),
                f("author", "text", "Author", required=True, max_len=80),
                f("role", "text", "Role / company", max_len=80),
                f("avatar_url", "image", "Photo"),
            ]),
        ],
    },
    "Pricing": {
        "label": "Pricing table",
        "description": "Plan comparison.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("tiers", "list", "Plans", fields=[
                f("name", "text", "Plan name", required=True, max_len=40),
                f("price_cents", "number", "Price (in cents)", required=True),
                f("period", "select", "Billing period", default="month",
                  options=["month", "year", "once"]),
                f("features", "list", "Features", fields=[
                    f("text", "text", "Feature", required=True, max_len=120)]),
                f("highlighted", "boolean", "Highlight this plan", default=False),
                f("cta_text", "text", "Button text", max_len=40),
                f("cta_link", "url", "Button link"),
            ]),
        ],
    },
    "FAQ": {
        "label": "FAQ",
        "description": "Expandable question and answer list.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("items", "list", "Questions", fields=[
                f("question", "text", "Question", required=True, max_len=200),
                f("answer", "textarea", "Answer", required=True, max_len=1500),
            ]),
        ],
    },
    "CTA": {
        "label": "Call to action",
        "description": "Single banner pushing one action.",
        "fields": [
            f("heading", "text", "Heading", required=True, max_len=120),
            f("body", "textarea", "Supporting text", max_len=300),
            f("cta_text", "text", "Button text", required=True, max_len=40),
            f("cta_link", "url", "Button link", required=True),
        ],
    },
    "ContactForm": {
        "label": "Contact form",
        "description": "Enquiry form. Submissions arrive as orders with status 'pending'.",
        "fields": [
            f("heading", "text", "Heading", max_len=120),
            f("fields", "list", "Fields to collect", fields=[
                f("name", "select", "Field", required=True,
                  options=["name", "email", "phone", "company", "message", "date"]),
                f("required", "boolean", "Required", default=True),
            ]),
            f("submit_text", "text", "Submit button text", default="Send", max_len=40),
            f("success_message", "textarea", "Thank-you message", max_len=300),
        ],
    },
    "Footer": {
        "label": "Footer",
        "description": "Bottom of every page.",
        "fields": [
            f("note", "textarea", "Small print", max_len=300),
            f("show_socials", "boolean", "Show social links", default=True,
              help_="Links come from the site's business details — set once, used everywhere."),
            f("links", "list", "Extra links", fields=_LINK),
        ],
    },
}


def block_types() -> list[str]:
    return sorted(REGISTRY)


def catalogue() -> list[dict]:
    """What GET /blocks returns — the admin panel builds its whole UI from this."""
    return [{"type": key, **spec} for key, spec in sorted(REGISTRY.items())]


def validate_block(block: Any, index: int = 0) -> dict:
    """Validate and normalise ONE block. Raises ValueError with a human message.

    Normalising matters as much as validating: it drops unknown keys and fills
    defaults, so what lands in JSONB is always the current shape. Without that,
    old junk keys accumulate for years and every template has to defend against
    them.
    """
    where = f"block #{index + 1}"
    if not isinstance(block, dict):
        raise ValueError(f"{where}: must be an object")

    btype = block.get("type")
    if btype not in REGISTRY:
        raise ValueError(
            f"{where}: unknown block type {btype!r}. "
            f"Valid types: {', '.join(block_types())}"
        )

    data = block.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError(f"{where} ({btype}): 'data' must be an object")

    clean = _validate_fields(REGISTRY[btype]["fields"], data, f"{where} ({btype})")
    out = {"type": btype, "data": clean}
    if block.get("id"):
        out["id"] = str(block["id"])
    return out


def _validate_fields(specs: list[dict], data: dict, where: str) -> dict:
    clean: dict[str, Any] = {}
    for spec in specs:
        name, ftype = spec["name"], spec["type"]
        value = data.get(name)

        if value in (None, ""):
            if spec["required"]:
                raise ValueError(f"{where}: '{spec['label']}' is required")
            clean[name] = spec.get("default") if spec.get("default") is not None else value
            continue

        if ftype == "list":
            if not isinstance(value, list):
                raise ValueError(f"{where}: '{spec['label']}' must be a list")
            clean[name] = [
                _validate_fields(spec["fields"], row if isinstance(row, dict) else {},
                                 f"{where} > {spec['label']} row {i + 1}")
                for i, row in enumerate(value)
            ]
        elif ftype == "boolean":
            clean[name] = bool(value)
        elif ftype == "number":
            try:
                clean[name] = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{where}: '{spec['label']}' must be a number") from None
        elif ftype == "select":
            if value not in spec.get("options", []):
                raise ValueError(
                    f"{where}: '{spec['label']}' must be one of "
                    f"{', '.join(spec.get('options', []))}"
                )
            clean[name] = value
        else:  # text, textarea, image, url
            text = str(value)
            if spec.get("max_len") and len(text) > spec["max_len"]:
                raise ValueError(
                    f"{where}: '{spec['label']}' is too long "
                    f"({len(text)}/{spec['max_len']} characters)"
                )
            clean[name] = text
    return clean


def validate_blocks(blocks: Any) -> list[dict]:
    """Validate a whole page's block array. Called on every page write."""
    if not isinstance(blocks, list):
        raise ValueError("blocks must be a list")
    return [validate_block(b, i) for i, b in enumerate(blocks)]
