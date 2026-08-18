---
name: add-resource
description: Add a new tenant-scoped CRUD resource to the API — e.g. funnels, coupons, subscribers, blog posts, form submissions. Use when the user wants new endpoints for a new kind of record, or mentions adding a resource, model, or router.
---

# Add a tenant-scoped CRUD resource

Follow the existing shape exactly. `app/api/commerce.py` is the reference — read
its category section before writing anything.

## Order of work

### 1. Migration first

Use the `/add-migration` skill. Do not write Python against a table that does not
exist yet.

### 2. Model in `app/models.py`

Reuse `_pk()`, `_created()`, `_updated()`, `TimestampMixin`. Include `tenant_id`
and (if it belongs to a storefront) `site_id`.

For a collection relationship, set `lazy="selectin"` — without it, listing 50
parents fires 51 queries.

### 3. Schemas in `app/schemas.py`

Three models, never one:

```python
class ThingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = None          # auto-derived when omitted

class ThingUpdate(BaseModel):        # every field optional
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None

class ThingOut(ORMModel):            # ORMModel = from_attributes
    id: uuid.UUID
    site_id: uuid.UUID
    name: str
    created_at: datetime
```

A single shared model would let a client PATCH `tenant_id` or `id`. On these
models those fields do not exist, so they cannot be sent.

### 4. Router

Add to an existing router if it fits the domain; otherwise create
`app/api/things.py` and register it in `app/api/__init__.py`.

```python
router = APIRouter(tags=["things"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("/sites/{site_id}/things", response_model=Page[ThingOut])
async def list_things(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)
    rows, total = await crud.list_scoped(
        db, Thing, user.tenant_id,
        filters=[Thing.site_id == site_id],
        order_by=Thing.created_at.desc(), limit=limit, offset=offset,
    )
    # Plain dict, NOT Page(...) — Pydantic rejects the unparameterised class.
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post("/sites/{site_id}/things", response_model=ThingOut, status_code=201)
async def create_thing(
    site_id: uuid.UUID, payload: ThingCreate, user: CurrentUser, db: DB
) -> Thing:
    site = await _owned_site(db, user.tenant_id, site_id)
    data = payload.model_dump()
    data["slug"] = payload.slug or crud.slugify(payload.name, "thing")
    # tenant_id from the VERIFIED parent, never from the request body.
    thing = Thing(site_id=site.id, tenant_id=site.tenant_id, **data)
    return await crud.save(db, thing)


@router.patch("/sites/{site_id}/things/{thing_id}", response_model=ThingOut)
async def update_thing(
    site_id: uuid.UUID, thing_id: uuid.UUID, payload: ThingUpdate,
    user: CurrentUser, db: DB,
) -> Thing:
    await _owned_site(db, user.tenant_id, site_id)
    thing = await crud.get_scoped(db, Thing, user.tenant_id, thing_id)
    crud.apply_updates(thing, payload.model_dump(exclude_unset=True))
    return await crud.save(db, thing)


@router.delete("/sites/{site_id}/things/{thing_id}", status_code=204)
async def delete_thing(
    site_id: uuid.UUID, thing_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    thing = await crud.get_scoped(db, Thing, user.tenant_id, thing_id)
    await crud.delete(db, thing)
```

### 5. Non-negotiables

- **Never** `select(Thing).where(Thing.id == x)` in a router. Always
  `crud.get_scoped` / `crud.list_scoped`.
- Nested routes call `_owned_site()` **first**, before anything else.
- If the resource affects what visitors see, call
  `await cache.invalidate_site(site.subdomain, site.custom_domain)` after every
  mutation.
- Add new UNIQUE constraint names to `crud._explain` so violations become readable
  409s instead of "That value conflicts with an existing record."
- Cross-site references (a parent id from another site) must be rejected — see
  `create_category` in `commerce.py`.

### 6. Tests — both files

`tests/test_<resource>.py` for behaviour, and **add isolation tests to
`tests/test_tenant_isolation.py`**:

```python
async def test_other_tenant_cannot_see_things(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)
    created = await a.post(f"/sites/{site['id']}/things", json={"name": "Secret"})
    thing_id = created.json()["id"]

    assert (await b.get(f"/sites/{site['id']}/things/{thing_id}")).status_code == 404
    assert (await b.get(f"/sites/{site['id']}/things")).status_code == 404
```

A tenant-owned resource without isolation tests is not finished.

### 7. Optional: Postman

Add a folder to `postman/collection.json` following the existing pattern, with a
test script that captures the new id into a collection variable.

## Then

Run `pytest` and report the result. Do not start the server — the user does that.
