"""SQLAlchemy models — the Python mirror of migrations/*.sql.

IMPORTANT: these classes do NOT create tables. The SQL files in migrations/ are
the source of truth for schema, indexes and constraints (that's where the real
database engineering lives — expression indexes, partial indexes, INCLUDE
columns and triggers have no clean ORM equivalent).

These models exist only so Python can query and write those tables in a typed
way. If you change a column here, change the matching SQL file too, or the two
will drift.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    # server_default only. The database trigger maintains this column, so we
    # deliberately do NOT set onupdate= here — that would let Python overwrite
    # the trigger's value and hide drift between app writes and manual SQL edits.
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin:
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# =============================================================================
#  Accounts
# =============================================================================


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(Text, default="demo")
    status: Mapped[str] = mapped_column(Text, default="active")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Legal/tax identity for billing & invoicing — distinct from any site's
    # customer-facing Site.business (contact info shown on the storefront).
    business: Mapped[dict] = mapped_column(JSONB, default=dict)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Personal contact number — distinct from Site Settings → Contact Info's
    # business phone/whatsapp (Site.business), which is customer-facing.
    # This one is for account-level contact (e.g. a future 2FA/security
    # alert channel), so it lives on the user, not the site.
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Either a real Cloudinary URL (uploaded or picked from the site's media
    # library) or a generated data: URI for one of the picker's preset
    # silhouette avatars — both are just plain URL strings from here on,
    # so nothing downstream needs to special-case which kind it is.
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # IANA name (e.g. "Asia/Dhaka") — lets order timestamps and notification
    # digests render in the merchant's own local time instead of UTC/browser
    # guesswork. Free text for now, matching Site.business's other loosely
    # typed fields; a picker can validate against a real IANA list later.
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")


# =============================================================================
#  Sites
# =============================================================================


class Template(Base, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    framework: Mapped[str] = mapped_column(Text, default="nextjs")
    block_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    default_theme: Mapped[dict] = mapped_column(JSONB, default=dict)
    default_pages: Mapped[list] = mapped_column(JSONB, default=list)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which Vercel project serves this template's sites — see
    # app/vercel.py and publish_site's JOB_ATTACH_DOMAIN. Not exposed in
    # TemplateOut; this is deployment plumbing, not customer-facing data.
    vercel_project_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class Site(Base, TimestampMixin):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    subdomain: Mapped[str] = mapped_column(CITEXT, unique=True)
    custom_domain: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="draft")
    theme: Mapped[dict] = mapped_column(JSONB, default=dict)
    business: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Dedicated About Us page content — {heading, image, paragraphs: str[]}.
    # Deliberately separate from theme.tagline (short strapline shown site-
    # wide) and business.description (one-line SEO/contact blurb): this is
    # the merchant's actual longer-form story, editable from its own Site
    # Settings section rather than overloading either of those.
    about: Mapped[dict] = mapped_column(JSONB, default=dict)
    seo: Mapped[dict] = mapped_column(JSONB, default=dict)
    shipping: Mapped[dict] = mapped_column(JSONB, default=dict)
    faqs: Mapped[list] = mapped_column(JSONB, default=list)
    legal: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Checkout rules evaluated against the CURRENT order only (no order
    # history needed) — see dashboard/components/fraud/fraud-data.ts's
    # FRAUD_RULES for the exact shape: {rule_id: {enabled, value?}}.
    fraud_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Mobile-viewport screenshot of the live storefront, captured by the
    # worker right after a publish (see queue.JOB_CAPTURE_SCREENSHOT) — shown
    # on the Themes page card. System-generated, not a merchant upload, so it
    # lives outside media.py's VALID_CATEGORIES and never counts against the
    # plan's storage quota.
    screenshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    template: Mapped["Template"] = relationship(lazy="joined")
    pages: Mapped[list["SitePage"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class SitePage(Base, TimestampMixin):
    __tablename__ = "site_pages"
    __table_args__ = (UniqueConstraint("site_id", "slug", name="uq_site_pages_site_slug"),)

    id: Mapped[uuid.UUID] = _pk()
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    slug: Mapped[str] = mapped_column(CITEXT)
    title: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list] = mapped_column(JSONB, default=list)
    seo: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    site: Mapped["Site"] = relationship(back_populates="pages")


# =============================================================================
#  Commerce
# =============================================================================


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("site_id", "slug", name="uq_categories_site_slug"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(CITEXT)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wide cover image for the shop page's per-category banner — separate
    # from image_url, which is the small thumbnail used in listings/cards.
    banner_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A lucide-react icon name (e.g. "Truck"), not an uploaded image — only
    # templates with an icon-driven category directory (e.g. Bazaar) render
    # this; others ignore it.
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("site_id", "slug", name="uq_products_site_slug"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    sku: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(CITEXT)
    # Sanitized HTML from the dashboard's rich text editor, not plain text.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SEO/search-snippet length — kept separate from description so a <meta>
    # tag never ends up rendering a chunk of rich HTML.
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    compare_at_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Merchant's own cost basis — optional, never exposed on any public/
    # storefront endpoint (see migrations/036_product_cost_price.sql).
    cost_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    images: Mapped[list] = mapped_column(JSONB, default=list)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Uploaded Cloudinary video URL or a pasted external link (YouTube etc).
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Merchant-entered "starting" sold count for social proof — not a real
    # sales figure, see ProductOut's comment in schemas.py.
    initial_sold_count: Mapped[int] = mapped_column(Integer, default=0)
    free_delivery: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_charge_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{name, charge_cents}, ...] — multiple delivery-charge options a
    # merchant can offer for this product (e.g. "Inside Dhaka" / "Outside
    # Dhaka"), each a snapshot of one of the site's shipping locations at
    # the time it was added. The storefront checkout lets a customer pick
    # among whichever of these apply to everything in their cart.
    delivery_charges: Mapped[list] = mapped_column(JSONB, default=list)
    # [{title, description}, ...] — icon callouts on the storefront product
    # page. Empty list (not omitted) means "nothing to show", same honesty
    # rule as everything else added this pass.
    features: Mapped[list] = mapped_column(JSONB, default=list)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("site_id", "phone", name="uq_customers_site_phone"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    # Dedup key, not email — these are COD-heavy stores where phone is
    # collected on every order and email frequently isn't.
    phone: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("site_id", "order_number", name="uq_orders_site_number"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    order_number: Mapped[str] = mapped_column(Text)
    customer: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Nullable and SET NULL on delete — order history is immutable (see
    # CLAUDE.md rule 8), so deleting a customer record must never delete or
    # orphan the orders it's linked to; the order's own `customer` JSONB
    # snapshot above still has everything needed to render the past order.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, default="pending")
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    # lazy="selectin": loads all items for all orders in ONE extra query rather
    # than one query per order (the classic N+1 problem). For a list of 50
    # orders that is 2 queries instead of 51.
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class Inquiry(Base):
    __tablename__ = "inquiries"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(Text, default="new")
    created_at: Mapped[datetime] = _created()


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_qty"),
    )

    id: Mapped[uuid.UUID] = _pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    name_snapshot: Mapped[str] = mapped_column(Text)
    sku_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    # Cost at the time this was sold, not today's cost — order history is
    # immutable (CLAUDE.md rule 8), same reasoning as the snapshots above.
    # Null when the product had no cost set at sale time.
    cost_price_cents_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()

    order: Mapped["Order"] = relationship(back_populates="items")


class CourierConnection(Base, TimestampMixin):
    """A site's own courier merchant account (Steadfast/Pathao/RedX), never a
    shared platform key. See migrations/012_courier_connections.sql — that
    file is the real source of truth for constraints/indexes.

    Credentials live only as Fernet ciphertext (app/courier_crypto.py).
    api_key_hint is the sole credential fragment ever safe to serialize.
    """

    __tablename__ = "courier_connections"
    __table_args__ = (
        UniqueConstraint("site_id", "provider", name="uq_courier_connections_site_provider"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="connected")
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    secret_key_encrypted: Mapped[str] = mapped_column(Text)
    api_key_hint: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaymentConnection(Base, TimestampMixin):
    """A site's own storefront payment method (COD, manual bKash/Nagad
    instructions, or a gateway merchant account). See
    migrations/009_payments_and_fraud.sql — that file is the real source of
    truth for constraints/indexes.

    cod/manual have no credentials — `config` holds their real settings
    instead. Gateway providers (bkash/nagad/sslcommerz/rocket) use the
    encrypted columns, reusing app/courier_crypto.py's Fernet key (same
    trust boundary as courier credentials). api_key_hint is the sole
    credential fragment ever safe to serialize.
    """

    __tablename__ = "payment_connections"
    __table_args__ = (
        UniqueConstraint("site_id", "provider", name="uq_payment_connections_site_provider"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="connected")
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-secret settings: cod_fee_cents, payment_number, wallets, merchant_id
    # — whichever apply to this provider. Never api_key/secret_key.
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_hint: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketingConnection(Base, TimestampMixin):
    """A site's own marketing/tracking integration credential — currently
    just Meta Conversions API (server-side Purchase events). See
    migrations/035_marketing_connections.sql.

    The client-side pixel IDs (Meta Pixel, TikTok Pixel, GTM container) live
    in Site.seo instead — they're not secrets, they're visible in every
    page's HTML source. Only the CAPI access token needs encryption, reusing
    app/courier_crypto.py's Fernet key (same trust boundary as courier/
    payment credentials). access_token_hint is the sole credential fragment
    ever safe to serialize.
    """

    __tablename__ = "marketing_connections"
    __table_args__ = (
        UniqueConstraint("site_id", "provider", name="uq_marketing_connections_site_provider"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="connected")
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    access_token_hint: Mapped[str] = mapped_column(Text)


class FraudBlocklistEntry(Base):
    """A merchant-maintained phone number blocklist — not a computed risk
    score. There is no order-history data to score a customer against yet
    (see the fraud page's design notes); this is the one thing that works
    with zero history, since the merchant already knows the number is bad.

    Base only, not TimestampMixin: entries are add/delete only, never
    updated in place, so there's no updated_at column or trigger for one
    (see migrations/009_payments_and_fraud.sql).
    """

    __tablename__ = "fraud_blocklist"
    __table_args__ = (
        UniqueConstraint("site_id", "phone", name="uq_fraud_blocklist_site_phone"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    phone: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created()


class PageView(Base):
    """One storefront page load — real visitor/traffic data, see
    migrations/037_page_views.sql. Fired best-effort from the client (see
    app/api/public.py's log_page_view); never blocks a page render.

    Base only, not TimestampMixin: rows are insert-only, never updated.

    session_id is a random id the storefront generates once into
    localStorage — not a real identity, just enough to count unique
    visitors, never PII or a cookie-consent-grade tracking mechanism.
    """

    __tablename__ = "page_views"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Notification(Base):
    """Dashboard bell notifications — created as a side effect of another
    write (order arrives, site published/unpublished), see
    app/notifications.py's notify(). Base only, not TimestampMixin: the only
    mutation is read_at going from null to a timestamp, set directly by the
    API — no separate updated_at/trigger needed for that.

    Site-scoped, not tenant-wide: the dashboard always operates against one
    currentSite, same as Orders/Analytics/etc (see
    migrations/019_notifications.sql).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class PushSubscription(Base):
    """One browser's Web Push subscription for one site — see
    app/push.py and migrations/020_push_subscriptions.sql. Base only: a
    subscription is created or deleted, never updated in place."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE")
    )
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class HelpTicket(Base, TimestampMixin):
    """Support ticket for the Help Desk page. Tenant-scoped, not site-scoped
    — support is handled at the account level even if a tenant has multiple
    sites. No admin/agent reply flow yet (soft launch): a human replies by
    email out of band, same as the ticket form's own copy already says.
    """

    __tablename__ = "help_tickets"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(Text, default="Medium")
    status: Mapped[str] = mapped_column(Text, default="Open")
    message: Mapped[str] = mapped_column(Text)


class OrderCounter(Base):
    """One row per site — an atomically-incrementing order number source.

    Replaces counting existing orders (see crud.next_order_number's
    docstring for why that doesn't scale). No TimestampMixin: this row is
    only ever incremented via UPDATE ... RETURNING, never read/displayed.
    """

    __tablename__ = "order_counters"

    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    next_number: Mapped[int] = mapped_column(Integer, default=1000)
