"""Maps source names (keys under `sources:` in a profile) to plug-in instances."""
from sources.base import Source
from sources.openfda_source import OpenFDA510kSource
from sources.research_sources import ArxivSource, ClinicalTrialsSource, PubMedSource
from sources.sec_form_d_source import SecFormDSource
from sources.tavily_source import TavilySource

SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        TavilySource(),
        OpenFDA510kSource(),
        SecFormDSource(),
        PubMedSource(),
        ArxivSource(),
        ClinicalTrialsSource(),
    ]
}


def get_source(name: str) -> Source | None:
    return SOURCES.get(name)
