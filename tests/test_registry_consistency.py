"""Guards against DRIFT between the three things that must agree about blocks:

    app/blocks.py          - the field schemas (the authority)
    templates.block_types  - what each template claims it can render
    templates.default_pages - the content a new site is seeded with

Nothing enforces this at runtime, because seeded default_pages are copied into a
new site without validation (deliberately — a bad seed should not break signup).
So it is checked here instead. If this file fails, a customer would create a site
whose content the editor then refuses to save — a genuinely confusing bug.

These tests need no HTTP client and no tenant. They are pure data checks.
"""

from sqlalchemy import select

from app import blocks as registry
from app.db import SessionLocal
from app.models import Template


async def _templates() -> list[Template]:
    async with SessionLocal() as db:
        return list((await db.execute(select(Template))).scalars().all())


async def test_seed_templates_exist():
    templates = await _templates()
    assert templates, "No templates. Run migrations/004_seed.sql in Supabase."


async def test_declared_block_types_all_exist_in_registry():
    """A template must not advertise a block the backend has never heard of."""
    known = set(registry.block_types())
    for template in await _templates():
        unknown = set(template.block_types) - known
        assert not unknown, (
            f"Template '{template.key}' declares block types that are missing from "
            f"app/blocks.py: {sorted(unknown)}"
        )


async def test_default_pages_pass_block_validation():
    """The seeded content must survive the same validator a Save would run."""
    for template in await _templates():
        for page in template.default_pages or []:
            try:
                registry.validate_blocks(page.get("blocks", []))
            except ValueError as exc:
                raise AssertionError(
                    f"Template '{template.key}', page '{page.get('slug', '')}' has "
                    f"invalid seed content: {exc}"
                ) from exc


async def test_default_pages_only_use_declared_blocks():
    """Seed content must stay inside the template's own manifest, or the template
    repo will be handed a block it has no component for."""
    for template in await _templates():
        declared = set(template.block_types)
        for page in template.default_pages or []:
            used = {b.get("type") for b in page.get("blocks", [])}
            assert used <= declared, (
                f"Template '{template.key}' seeds blocks it does not declare: "
                f"{sorted(used - declared)}"
            )


def test_registry_field_specs_are_well_formed():
    """Every field needs the keys the admin panel's form generator reads."""
    required_keys = {"name", "type", "label", "required", "default", "help"}
    valid_types = {
        "text", "textarea", "image", "url", "number", "boolean", "select", "list",
    }

    def check(fields: list[dict], path: str) -> None:
        for field in fields:
            missing = required_keys - set(field)
            assert not missing, f"{path}.{field.get('name')} missing keys: {missing}"
            assert field["type"] in valid_types, (
                f"{path}.{field['name']} has unknown type {field['type']!r}"
            )
            if field["type"] == "select":
                assert field.get("options"), (
                    f"{path}.{field['name']} is a select with no options"
                )
            if field["type"] == "list":
                assert field.get("fields"), (
                    f"{path}.{field['name']} is a list with no sub-fields"
                )
                check(field["fields"], f"{path}.{field['name']}[]")

    for block_type, spec in registry.REGISTRY.items():
        assert "label" in spec and "fields" in spec, f"{block_type} spec incomplete"
        check(spec["fields"], block_type)


def test_required_fields_have_no_default():
    """A required field with a default can never actually fail validation, which
    makes the 'required' flag a lie the admin panel would render as an asterisk."""
    for block_type, spec in registry.REGISTRY.items():
        for field in spec["fields"]:
            if field["required"]:
                assert field["default"] is None, (
                    f"{block_type}.{field['name']} is required but has a default"
                )
