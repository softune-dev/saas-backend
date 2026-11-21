"""The merchant's assistant settings, and the one place that defines their shape.

sites.assistant is JSONB, which enforces nothing, so this model IS the schema
(CLAUDE.md rule 4 applies the same reasoning to site_pages.blocks). Anything
that writes the column must go through `AssistantSettings`. Reads are
forgiving on purpose: unknown or old keys are ignored and bad values fall back
to defaults, because a stored row from an older version must never take a
customer-facing chat down.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_QA = 100
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class QA(BaseModel):
    """One question the merchant answered in their own words."""

    model_config = ConfigDict(extra="ignore")
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    a: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]


class AssistantSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # ON by default, including for every store that existed before this column.
    enabled: bool = True
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = "Assistant"
    # An id from the dashboard's avatar gallery, or an https image URL.
    avatar: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = "bot"
    greeting: Short = ""
    launcher_text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = "Let's Talk"
    position: Literal["left", "right"] = "right"
    # None = follow the theme's primary colour.
    accent: str | None = None
    qa: list[QA] = Field(default_factory=list, max_length=MAX_QA)

    @field_validator("accent")
    @classmethod
    def _accent(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not _HEX.match(v):
            raise ValueError("accent must be a #RRGGBB colour")
        return v.upper()

    @field_validator("avatar")
    @classmethod
    def _avatar(cls, v: str) -> str:
        if v.startswith(("http://", "https://")) and not v.startswith("https://"):
            raise ValueError("avatar URL must be https")
        return v or "bot"


def settings_from_row(raw: Any) -> AssistantSettings:
    """Forgiving read of sites.assistant: bad or missing data = defaults."""
    try:
        return AssistantSettings.model_validate(raw or {})
    except Exception:  # noqa: BLE001 - a bad stored row must not break the chat
        return AssistantSettings()


class AskIn(BaseModel):
    # Capped: this is an anonymous public endpoint, and the text is only ever
    # tokenised, never stored or sent anywhere.
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
