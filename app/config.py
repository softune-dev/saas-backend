"""Typed settings loaded from .env — the single place env vars are read.

Nothing else in the codebase calls os.environ. That means a missing or malformed
variable fails loudly at startup with a clear message, instead of surfacing as a
confusing None deep inside a request three days later.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app ---
    app_env: str = "development"
    debug: bool = True
    port: int = 8000

    # --- auth ---
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # --- database ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str
    direct_url: str = ""

    # --- cache ---
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300

    # --- queue ---
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    queue_name: str = "saas_jobs"

    # --- misc ---
    cors_origins: str = "http://localhost:3000"
    revalidate_secret: str = ""
    site_base_domain: str = "vercel.app"

    # Where THIS backend is publicly reachable — gateway checkout callbacks
    # (bKash/SSLCommerz/Nagad redirecting the customer's browser, or posting
    # an IPN) need a real, internet-reachable URL to come back to, which is
    # never the storefront's own domain (that's a static Next.js app, no
    # server-side route to receive these). See app/api/public.py's payment
    # init/callback endpoints.
    api_base_url: str = "https://api.softunebd.com"

    # --- Vercel domain automation (app/vercel.py) ---
    # Blank-able like Cloudinary above: attaching a site's subdomain to the
    # right Vercel project on publish is a nice-to-have, not a hard
    # dependency — see vercel.py's own graceful-degradation comment.
    vercel_api_token: str = ""
    # Only needed if the Vercel projects live under a Team, not a personal
    # account — leave blank otherwise.
    vercel_team_id: str = ""

    # --- media (Cloudinary) ---
    # See app/media.py for how these are used. Left blank-able rather than
    # required: the rest of the API must keep working for anyone who hasn't
    # set up media storage yet, and app/media.py raises its own clear error
    # only when an upload is actually attempted without them.
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    # Root folder every site's media lives under, e.g. "softune". Final layout
    # is {root}/{site-subdomain}/{hero|products|categories|other}/.
    cloudinary_root_folder: str = "softune"

    # --- courier integrations ---
    # Fernet key encrypting courier API credentials at rest (see
    # app/courier_crypto.py) — a merchant's Steadfast/Pathao/RedX secret key
    # is as sensitive as a payment credential and must never sit in the
    # database as plaintext. Blank-able for the same reason as the Cloudinary
    # keys above: the rest of the API keeps working, and only an actual
    # courier-connect attempt fails, with a message telling you what's missing.
    courier_credentials_key: str = ""

    # --- web push (browser push notifications for new orders) ---
    # Generated once per deployment (see app/push.py's module docstring for
    # the one-off generation command) — the private key signs push messages
    # so only this server can send to a subscribed browser; the public key
    # ships to the dashboard (NEXT_PUBLIC_VAPID_PUBLIC_KEY) so it can create
    # a subscription in the first place. Blank-able like the other
    # integration keys: without them, notify()/push just skips sending.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:support@softunebd.com"

    # --- AI theme assistant (Gemini) ---
    # Blank-able like the keys above: the rest of the API keeps working, and
    # only the AI-suggest endpoint (app/api/ai.py) fails, with a clear
    # message, until this is set. Server-side only — never sent to the
    # browser, unlike Cloudinary's keys which the SDK needs client-adjacent.
    gemini_api_key: str = ""

    # --- reCAPTCHA (login, checkout, contact form) ---
    # Same blank-able convention: unset locally, verification just skips
    # (see app/recaptcha.py) instead of failing every request until it's set.
    # Server-side only — the SITE key (public, safe in the browser) lives in
    # NEXT_PUBLIC_RECAPTCHA_SITE_KEY (dashboard and both storefront templates).
    recaptcha_secret_key: str = ""
    # A SEPARATE key, registered as "v2 Checkbox" in the reCAPTCHA admin
    # console (v3 and v2 are different site/secret key pairs, even for the
    # same domain) — used only as a fallback when v3 scores a request too
    # low to auto-approve. Blank means no fallback exists: a low v3 score is
    # then a straight rejection, same as before this existed.
    recaptcha_v2_secret_key: str = ""
    # Flash-Lite, not Pro — this only ever suggests a handful of theme fields
    # or answers store questions via read-only tools, not deep reasoning, so
    # the cheapest tier is the right default. Uses the "-latest" alias, not a
    # dated snapshot: Google retires dated Flash snapshots within months, and
    # a 404 here means the whole assistant breaks until someone notices and
    # bumps this string. Overridable per-deployment if you want a pinned id.
    gemini_model: str = "gemini-flash-lite-latest"
    # Simple per-tenant-per-day cap (see app/ai.py) so a single site can't
    # run up a surprise bill. Generous enough for real iterative use.
    ai_suggestions_per_tenant_per_day: int = 50

    # --- trial signup / demo access (app/api/trial.py, app/api/public.py) ---
    # Real SMTP send via Hostinger's mail server — the OTP email is the
    # first thing this codebase actually sends (see app/mailer.py); nothing
    # before this sent real email. Blank-able like every other integration
    # key: the rest of the API keeps working, and only the signup endpoint
    # fails, with a clear message, until this is set.
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "support@softunebd.com"
    smtp_from_name: str = "Softune"
    # Landing's own hosted logo — the icon mark (not the wordmark), for the
    # email header badge.
    email_logo_url: str = "https://www.softunebd.com/logo-icon.png"
    # A signup's pre-verification state (email/password/OTP/shop basics)
    # lives in Redis, not Postgres — see app/api/trial.py's module
    # docstring. This is that blob's TTL: long enough to fill out a
    # multi-step form, short enough that an abandoned signup just vanishes
    # on its own instead of needing manual cleanup.
    trial_signup_ttl_minutes: int = 45
    # How long a trial tenant can log in for. Login is rejected once
    # Tenant.trial_expires_at (trial_started_at + this) passes — see
    # app/api/auth.py.
    trial_days: int = 3
    # Extra days AFTER trial_expires_at before app/worker.py's sweep hard-
    # deletes the tenant — total lifetime of an unpurchased trial is
    # trial_days + this. A real purchase within that window just changes
    # `plan` away from "trial", which takes it out of the sweep's filter.
    trial_grace_days: int = 4
    # Which real (plan="demo") tenant's login the public "See a live demo"
    # button mints a token for (app/api/public.py) — a shared, read-only
    # account, decoupled from trial signup entirely. Not a secret: this
    # identifies WHICH account, app/security.py mints the token itself, no
    # password involved.
    demo_user_email: str = "kallol.business.ds@gmail.com"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process, not per request."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
