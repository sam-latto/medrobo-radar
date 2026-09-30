from datetime import date as date_type
from email.utils import parsedate_to_datetime
from pydantic import BaseModel, ValidationInfo, field_validator
from typing import Optional

from profiles.loader import FALLBACK_EVENT_TYPE, FALLBACK_SEGMENT, normalize_key


def normalize_date(value) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    try:
        return date_type.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    if len(text) == 7:  # YYYY-MM
        try:
            return date_type.fromisoformat(text + "-01").isoformat()
        except ValueError:
            pass
    try:  # RFC 2822, e.g. "Mon, 15 Sep 2026 10:00:00 GMT"
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


class RawResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    date: Optional[str] = None
    query_tag: str
    source_type: str = "tavily"


class ExtractedEvent(BaseModel):
    """One structured event.

    Validate with `ExtractedEvent.model_validate(data, context={"profile": profile})`
    to coerce event_type/segment to the profile's vocabulary (unknown -> news/other).
    Without a profile in context, values are only normalized.
    """

    industry_id: Optional[str] = None
    company: Optional[str] = None
    company_id: Optional[int] = None
    product: Optional[str] = None
    event_type: str
    segment: str = FALLBACK_SEGMENT
    date: Optional[str] = None
    summary: str
    source_url: str
    source_type: str = "tavily"
    funding_amount: Optional[int] = None
    regulatory_status: Optional[str] = None
    is_notable: bool = False

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v) -> Optional[str]:
        """Normalize to YYYY-MM-DD; anything unparseable (e.g. '<UNKNOWN>') becomes None."""
        return normalize_date(v)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str, info: ValidationInfo) -> str:
        profile = (info.context or {}).get("profile")
        if profile is not None:
            return profile.coerce_event_type(v)
        return normalize_key(v) or FALLBACK_EVENT_TYPE

    @field_validator("segment")
    @classmethod
    def validate_segment(cls, v: str, info: ValidationInfo) -> str:
        profile = (info.context or {}).get("profile")
        if profile is not None:
            return profile.coerce_segment(v)
        return normalize_key(v) or FALLBACK_SEGMENT

