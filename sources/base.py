from datetime import date
from typing import Protocol

from pipeline.models import RawResult
from profiles.loader import IndustryProfile


class Source(Protocol):
    """A data-source plug-in. Profiles enable sources by name under `sources:`."""

    name: str                  # e.g. "openfda_510k"; the key used in profile YAML
    source_type: str           # stored on events, e.g. "openfda"
    description: str           # shown to the profile-drafting agent
    config_schema: dict        # JSON schema for this source's profile config block
    default_lookback_days: int

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]: ...
