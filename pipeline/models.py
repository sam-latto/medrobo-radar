from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import date


class RawResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    date: Optional[str] = None
    query_tag: str


class ExtractedEvent(BaseModel):
    company: Optional[str] = None
    product: Optional[str] = None
    event_type: str
    sub_segment: str
    date: Optional[str] = None
    summary: str
    source_url: str
    funding_amount: Optional[int] = None
    fda_status: Optional[str] = None
    is_notable: bool = False

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        allowed = {"funding", "fda", "launch", "research", "news"}
        v = v.lower().strip()
        if v not in allowed:
            v = "news"
        return v

    @field_validator("sub_segment")
    @classmethod
    def validate_sub_segment(cls, v: str) -> str:
        allowed = {"surgical", "rehabilitation", "diagnostics", "exoskeletons", "ai_assisted", "other"}
        v = v.lower().strip().replace("-", "_").replace(" ", "_")
        if v not in allowed:
            v = "other"
        return v


class SynthesisOutput(BaseModel):
    briefing_text: str
    notable_event_ids: list[int] = []
