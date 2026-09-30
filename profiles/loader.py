"""Load, validate and save industry profiles (one YAML file per industry)."""
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROFILES_DIR = Path(__file__).parent

FALLBACK_EVENT_TYPE = "news"
FALLBACK_SEGMENT = "other"


def normalize_key(value: str) -> str:
    """'AI-assisted' -> 'ai_assisted'."""
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


class NotableRules(BaseModel):
    funding_threshold_usd: int = 50_000_000
    always_notable_event_types: list[str] = []


class IndustryProfile(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    display_name: Optional[str] = None
    description: str = ""
    active: bool = True
    segments: list[str]
    event_types: list[str]
    search_queries: dict[str, str] = {}
    notable_rules: NotableRules = NotableRules()
    sources: dict[str, dict] = {"tavily": {}}
    watchlist: list[str] = []
    briefing_audience: str = "sophisticated industry investor"

    @field_validator("segments", "event_types")
    @classmethod
    def _normalize_list(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            key = normalize_key(item)
            if key and key not in out:
                out.append(key)
        if not out:
            raise ValueError("must not be empty")
        return out

    @field_validator("sources", mode="before")
    @classmethod
    def _sources_default_configs(cls, v):
        # Allow `tavily:` with no body in YAML (parses as None)
        return {name: (cfg or {}) for name, cfg in (v or {}).items()}

    @model_validator(mode="after")
    def _check_consistency(self) -> "IndustryProfile":
        if FALLBACK_EVENT_TYPE not in self.event_types:
            self.event_types.append(FALLBACK_EVENT_TYPE)
        self.search_queries = {normalize_key(k): q for k, q in self.search_queries.items()}
        unknown = set(self.search_queries) - set(self.event_types)
        if unknown:
            raise ValueError(f"search_queries has unknown event types: {sorted(unknown)}")
        self.notable_rules.always_notable_event_types = [
            key for key in (normalize_key(t) for t in self.notable_rules.always_notable_event_types)
            if key in self.event_types
        ]
        return self

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def segment_choices(self) -> list[str]:
        """Segments an event may be tagged with (profile segments + fallback)."""
        return self.segments + ([FALLBACK_SEGMENT] if FALLBACK_SEGMENT not in self.segments else [])

    def coerce_event_type(self, value: str) -> str:
        key = normalize_key(value or "")
        return key if key in self.event_types else FALLBACK_EVENT_TYPE

    def coerce_segment(self, value: str) -> str:
        key = normalize_key(value or "")
        return key if key in self.segment_choices else FALLBACK_SEGMENT

    def to_yaml(self) -> str:
        data = self.model_dump(exclude_none=True)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _profile_path(profile_id: str) -> Path:
    return PROFILES_DIR / f"{profile_id}.yaml"


def parse_profile(text: str) -> IndustryProfile:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must be a mapping")
    return IndustryProfile.model_validate(data)


def load_profile(profile_id: str) -> IndustryProfile:
    path = _profile_path(profile_id)
    if not path.exists():
        raise FileNotFoundError(f"No profile '{profile_id}' at {path}")
    profile = parse_profile(path.read_text(encoding="utf-8"))
    if profile.id != profile_id:
        raise ValueError(f"Profile file {path.name} declares id '{profile.id}'")
    return profile


def load_profiles(active_only: bool = False) -> list[IndustryProfile]:
    profiles = [load_profile(p.stem) for p in sorted(PROFILES_DIR.glob("*.yaml"))]
    return [p for p in profiles if p.active] if active_only else profiles


def profile_exists(profile_id: str) -> bool:
    return _profile_path(profile_id).exists()


def save_profile(profile: IndustryProfile, overwrite: bool = False) -> Path:
    path = _profile_path(profile.id)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Profile '{profile.id}' already exists")
    path.write_text(profile.to_yaml(), encoding="utf-8")
    return path
