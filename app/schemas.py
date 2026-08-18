"""Pydantic request/response models — the API's contract.

Two rules followed throughout:

1. SEPARATE Create / Update / Read models per resource. It looks repetitive but
   prevents a real bug class: a single shared model lets a client PATCH fields
   they must never control (tenant_id, id, created_at). Here those fields simply
   do not exist on the input models, so they cannot be sent.

2. `*Update` models have every field optional, and routers apply them with
   `exclude_unset=True`, so PATCH means "change what I sent" — not "null
   everything I omitted".
"""

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    # from_attributes lets a response model be built straight from a SQLAlchemy
    # object without a manual dict conversion step.
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """Envelope for every list endpoint. Consistent shape means the admin panel
    writes ONE pagination component instead of one per resource."""

    items: list[T]
    total: int
    limit: int
    offset: int


# =============================================================================
#  Auth
# =============================================================================


class RegisterIn(BaseModel):
    email: EmailStr
    # max_length=72 matches bcrypt's hard limit — see app/security.py. Rejecting
    # here gives a clear validation error instead of silently truncating.
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = Field(default=None, max_length=120)
    # The organisation created alongside the first user.
    workspace_name: str = Field(min_length=2, max_length=80)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str


class MeUpdate(BaseModel):
    # Only full_name — email changes need re-verification (not built yet) and
    # role/tenant are controlled elsewhere, so neither belongs on this form.
    full_name: str | None = Field(default=None, max_length=120)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds — lets the frontend refresh before expiry


class UserOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    created_at: datetime


class TenantOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    plan: str
    status: str
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut


# =============================================================================
#  Templates
# =============================================================================


class TemplateOut(ORMModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    framework: str
    block_types: list[str]
    thumbnail_url: str | None
    preview_url: str | None
    price_cents: int


# =============================================================================
#  Sites
# =============================================================================

_SUBDOMAIN_RE = r"^[a-z0-9][a-z0-9-]{1,40}[a-z0-9]$"

# Hostnames that would collide with your own infrastructure if a customer took
# them. Blocking at signup is far easier than migrating a customer off one later.
RESERVED_SUBDOMAINS = {
    "www", "api", "admin", "app", "dashboard", "mail", "ftp", "cdn", "static",
    "assets", "blog", "docs", "help", "support", "status", "billing", "auth",
    "login", "test", "staging", "dev", "preview",
}


class SiteCreate(BaseModel):
    template_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    subdomain: str = Field(pattern=_SUBDOMAIN_RE)

    @field_validator("subdomain")
    @classmethod
    def not_reserved(cls, v: str) -> str:
        if v.lower() in RESERVED_SUBDOMAINS:
            raise ValueError(f"'{v}' is reserved. Please choose another subdomain.")
        return v.lower()


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    custom_domain: str | None = None
    theme: dict[str, Any] | None = None
    business: dict[str, Any] | None = None
    about: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    shipping: dict[str, Any] | None = None
    faqs: list[dict[str, Any]] | None = None
    legal: dict[str, Any] | None = None
    fraud_rules: dict[str, Any] | None = None


class SiteOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    template_id: uuid.UUID
    name: str
    subdomain: str
    custom_domain: str | None
    status: str
    theme: dict
    business: dict
    about: dict
    seo: dict
    shipping: dict
    faqs: list
    legal: dict
    fraud_rules: dict
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DomainStatusOut(BaseModel):
    """connected=None means the check itself couldn't be answered (no
    Vercel token configured, or the request failed) — distinct from
    connected=False (checked, and DNS genuinely isn't pointed at Vercel
    yet). The dashboard shows a third "unknown" state for None rather than
    a false negative."""

    domain: str
    connected: bool | None


# =============================================================================
#  Pages
# =============================================================================


class PageCreate(BaseModel):
    # "" is the homepage. Anything else is a path segment.
    slug: str = Field(default="", max_length=60, pattern=r"^$|^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=120)
    # Shape is checked against app/blocks.py in the router, not here — the
    # registry is the authority on block shape, and duplicating those rules in
    # Pydantic would guarantee the two drift apart.
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    seo: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class PageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    blocks: list[dict[str, Any]] | None = None
    seo: dict[str, Any] | None = None
    is_published: bool | None = None
    sort_order: int | None = None


class PageOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    slug: str
    title: str
    blocks: list
    seo: dict
    is_published: bool
    sort_order: int
    updated_at: datetime


# =============================================================================
#  Commerce
# =============================================================================


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = None  # auto-derived from name when omitted
    description: str | None = None
    image_url: str | None = None
    banner_url: str | None = None
    icon: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = None
    description: str | None = None
    image_url: str | None = None
    banner_url: str | None = None
    icon: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CategoryOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    description: str | None
    image_url: str | None
    banner_url: str | None
    icon: str | None
    sort_order: int
    is_active: bool
    created_at: datetime


class ProductFeature(BaseModel):
    """One icon-callout on the storefront product page (e.g. "Free
    delivery" / "Worldwide shipping over ৳2,500"). Pydantic validates the
    shape here the same way validate_variants does for attributes.variants —
    this one's simple enough not to need its own module."""

    title: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)


class ProductDeliveryCharge(BaseModel):
    """One delivery-charge option a merchant offers for a product (e.g.
    "Inside Dhaka" for ৳60) — a snapshot of one of the site's shipping
    locations (Site Settings → Shipping) at the time it was added, not a
    live reference. If the merchant later edits that location's price in
    Settings, products that already added it keep the price they had —
    same "snapshot, don't silently reprice" instinct as order_items."""

    name: str = Field(min_length=1, max_length=60)
    charge_cents: int = Field(ge=0)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    sku: str | None = Field(default=None, max_length=60)
    # Rich HTML from the dashboard's editor — max_length is generous (a
    # product page is not a novel) but exists so no one JSONB-bombs the row.
    description: str | None = Field(default=None, max_length=20_000)
    short_description: str | None = Field(default=None, max_length=300)
    # ge=0 — a negative price is nonsense, and the database CHECK agrees. Both
    # layers guard it: the API gives a friendly 422, the database is the backstop.
    price_cents: int = Field(default=0, ge=0)
    compare_at_cents: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    stock: int = 0
    track_stock: bool = True
    category_id: uuid.UUID | None = None
    images: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    video_url: str | None = Field(default=None, max_length=2000)
    serial_number: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, max_length=20)
    initial_sold_count: int = Field(default=0, ge=0)
    free_delivery: bool = True
    delivery_charge_cents: int | None = Field(default=None, ge=0)
    delivery_charges: list[ProductDeliveryCharge] = Field(default_factory=list, max_length=10)
    features: list[ProductFeature] = Field(default_factory=list, max_length=8)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = None
    sku: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=20_000)
    short_description: str | None = Field(default=None, max_length=300)
    price_cents: int | None = Field(default=None, ge=0)
    compare_at_cents: int | None = Field(default=None, ge=0)
    stock: int | None = None
    track_stock: bool | None = None
    category_id: uuid.UUID | None = None
    images: list[dict[str, Any]] | None = None
    attributes: dict[str, Any] | None = None
    is_active: bool | None = None
    video_url: str | None = Field(default=None, max_length=2000)
    serial_number: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, max_length=20)
    initial_sold_count: int | None = Field(default=None, ge=0)
    free_delivery: bool | None = None
    delivery_charge_cents: int | None = Field(default=None, ge=0)
    delivery_charges: list[ProductDeliveryCharge] | None = Field(default=None, max_length=10)
    features: list[ProductFeature] | None = Field(default=None, max_length=8)


class ProductOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    category_id: uuid.UUID | None
    sku: str | None
    name: str
    slug: str
    description: str | None
    short_description: str | None
    price_cents: int
    compare_at_cents: int | None
    currency: str
    stock: int
    track_stock: bool
    images: list
    attributes: dict
    is_active: bool
    video_url: str | None
    serial_number: str | None
    unit: str | None
    # A merchant-entered starting count, NOT derived from real order history —
    # the storefront adds this to actual completed-order counts to show
    # "X sold" without a brand-new product looking untested. Dashboard UI
    # should label it accordingly so it's never mistaken for real analytics.
    initial_sold_count: int
    free_delivery: bool
    delivery_charge_cents: int | None
    delivery_charges: list[ProductDeliveryCharge]
    features: list[ProductFeature]
    created_at: datetime


class OrderItemIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=1000)


class OrderCreate(BaseModel):
    # Buyer details as a free-form object: what a florist needs to collect is not
    # what a consultancy needs. Validated shape would fight that variety.
    customer: dict[str, Any] = Field(default_factory=dict)
    items: list[OrderItemIn] = Field(min_length=1)
    shipping_cents: int = Field(default=0, ge=0)
    tax_cents: int = Field(default=0, ge=0)
    notes: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
#  Public checkout — what a real customer submits from the storefront
# =============================================================================
# Deliberately separate from OrderCreate/OrderItemIn above (the dashboard's
# own "create an order manually" endpoint): shipping_cents there is trusted
# from the request because it's an authenticated merchant typing it in. Here
# it must never be — an anonymous visitor could set it to 0. The public
# endpoint (app/api/public.py) computes it server-side from each product's
# own delivery_charges, the same way templates/aurora's CartContext.tsx
# computes it client-side for display, so the number the customer sees on
# the checkout page and the number that gets charged agree.


class PublicOrderItemIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=100)


class PublicOrderCreate(BaseModel):
    # Whatever the checkout form collects — name, phone, email, address,
    # city, postal code. Free-form for the same reason OrderCreate.customer
    # is: what one storefront's checkout asks for isn't universal.
    customer: dict[str, Any] = Field(default_factory=dict)
    items: list[PublicOrderItemIn] = Field(min_length=1)
    # Matches one of the delivery_charges names on the ordered products, or
    # null if none of them charge for delivery. Selects WHICH configured
    # charge applies; the actual cents come from the product row, not here.
    delivery_location: str | None = Field(default=None, max_length=120)
    payment_method: str = Field(default="cod", max_length=40)
    # Required for manual payment (bKash/Nagad/Rocket transfer) — the
    # merchant has no other way to match a submitted payment to this order.
    transaction_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def manual_needs_transaction_id(self) -> "PublicOrderCreate":
        if self.payment_method == "manual" and not (self.transaction_id or "").strip():
            raise ValueError("Manual payment requires a transaction ID.")
        return self


class PublicOrderItemOut(BaseModel):
    name: str
    quantity: int
    unit_price_cents: int
    total_cents: int


class PublicOrderOut(BaseModel):
    """Deliberately minimal — just enough for a checkout confirmation
    screen. Not ORMModel/OrderOut: this is public-facing, so it should never
    grow a field by inheriting whatever the internal Order model gains."""

    order_number: str
    items: list[PublicOrderItemOut]
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    delivery_location: str | None
    created_at: datetime


class OrderUpdate(BaseModel):
    # Only status and notes. Totals are immutable history — see the comment in
    # migrations/003_commerce.sql about why an order must not be recomputed.
    status: str | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str | None) -> str | None:
        allowed = {"pending", "paid", "fulfilled", "cancelled", "refunded"}
        if v is not None and v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return v


class OrderItemOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    name_snapshot: str
    sku_snapshot: str | None
    unit_price_cents: int
    quantity: int
    total_cents: int


class InquiryCreate(BaseModel):
    """Public — no auth. Shape is deliberately loose: each site's ContactForm
    block declares its own subset of fields (name/email/phone/message/...), so
    validating specific keys here would fight the registry instead of matching
    it. Length caps exist so a malicious visitor can't stuff megabytes into one
    JSONB row."""

    data: dict[str, Any] = Field(default_factory=dict, max_length=20)

    @field_validator("data")
    @classmethod
    def bounded_values(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key, value in v.items():
            if isinstance(value, str) and len(value) > 5000:
                raise ValueError(f"'{key}' is too long (max 5000 characters)")
        return v


class InquiryOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    data: dict
    status: str
    created_at: datetime


class OrderOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    order_number: str
    customer: dict
    status: str
    subtotal_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    currency: str
    notes: str | None
    # payment_method / delivery_location / transaction_id — see
    # app/api/public.py's create_public_order for what's actually written
    # here. Merchant-facing only; never exposed on PublicOrderOut.
    meta: dict
    items: list[OrderItemOut]
    created_at: datetime


# =============================================================================
#  Courier connections
# =============================================================================
# Shape matches dashboard/lib/api/courier.ts's contract exactly — that file
# was the spec this was built against, not the other way around.


class SteadfastConnectIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)
    secret_key: str = Field(min_length=1, max_length=200)
    base_url: str | None = None
    label: str | None = Field(default=None, max_length=80)


class CourierConnectionOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    provider: str
    status: str
    # Deliberately NOT api_key_encrypted/secret_key_encrypted — those columns
    # have no field here at all, so a future careless edit to this schema
    # can't accidentally leak ciphertext (or worse, plaintext) to the client.
    api_key_hint: str
    label: str | None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


# =============================================================================
#  Payment connections
# =============================================================================
# Shape matches dashboard/lib/api/payments.ts's contract — mirrors the
# courier connection pattern above. Providers with no credentials (cod,
# manual) leave api_key/secret_key unset; only config is used.

PaymentProvider = Literal["cod", "manual", "bkash", "nagad", "sslcommerz", "rocket"]


class PaymentConnectIn(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    # Non-secret settings — which of these apply depends on the provider,
    # validated server-side in app/api/payments.py rather than here, since
    # "required for this provider" isn't expressible per-field in one model
    # shared by six very different providers.
    cod_fee_cents: int | None = Field(default=None, ge=0)
    payment_number: str | None = Field(default=None, max_length=32)
    wallets: list[Literal["bkash", "nagad", "rocket"]] | None = None
    merchant_id: str | None = Field(default=None, max_length=120)
    # Gateway credentials — only meaningful for bkash/nagad/sslcommerz/rocket.
    api_key: str | None = Field(default=None, min_length=1, max_length=200)
    secret_key: str | None = Field(default=None, min_length=1, max_length=200)


class PaymentConnectionOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    provider: str
    status: str
    label: str | None
    config: dict
    # Deliberately NOT api_key_encrypted/secret_key_encrypted — same rule as
    # CourierConnectionOut above.
    api_key_hint: str | None
    created_at: datetime
    updated_at: datetime


# =============================================================================
#  Fraud blocklist
# =============================================================================
# Rules (hold_first_high_value / flag_burst_orders / block_blocklist) are NOT
# a separate model — they live in sites.fraud_rules, read/written through the
# existing SiteUpdate/SiteOut above, same as shipping/faqs/legal.


class FraudBlocklistEntryCreate(BaseModel):
    phone: str = Field(min_length=6, max_length=32)
    note: str = Field(default="", max_length=280)


class FraudBlocklistEntryOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    phone: str
    note: str
    created_at: datetime


# =============================================================================
#  Notifications
# =============================================================================
# Created as a side effect of other writes (app/notifications.py's notify())
# — there is no NotificationCreate; the client only lists and marks read.


class NotificationOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    type: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


# =============================================================================
#  Push subscriptions
# =============================================================================
# Shape matches the browser's PushSubscription.toJSON() exactly — see
# dashboard/lib/push.ts, which posts that object's keys/endpoint straight
# through with no reshaping.


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: PushSubscriptionKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
