"""Research sources: PubMed, arXiv and ClinicalTrials.gov."""
import logging
import xml.etree.ElementTree as ET
from datetime import date

from config import NCBI_API_KEY
from pipeline.models import RawResult
from profiles.loader import IndustryProfile
from sources import http

logger = logging.getLogger(__name__)


def _research_facts(profile: IndustryProfile, published: str | None) -> dict:
    return {"event_type": profile.coerce_event_type("research"), "date": published}


# ── PubMed ────────────────────────────────────────────────────────────────────

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def parse_pubmed_summaries(data: dict, profile: IndustryProfile) -> list[RawResult]:
    result = data.get("result", {})
    out: list[RawResult] = []
    for uid in result.get("uids", []):
        r = result.get(uid, {})
        title = (r.get("title") or "").strip()
        if not title:
            continue
        published = (r.get("sortpubdate") or "")[:10].replace("/", "-") or None
        authors = [a.get("name") for a in r.get("authors", [])[:3] if a.get("name")]
        snippet = (
            f"Peer-reviewed article in {r.get('fulljournalname') or r.get('source')} "
            f"({r.get('pubdate')}). Title: {title} Authors: {', '.join(authors)}"
            f"{' et al.' if len(r.get('authors', [])) > 3 else ''}. "
            f"Publication types: {', '.join(r.get('pubtype', []))}."
        )
        out.append(
            RawResult(
                title=title,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                snippet=snippet,
                source="pubmed.ncbi.nlm.nih.gov",
                date=published,
                query_tag=f"{profile.coerce_event_type('research')}:other",
                source_type=PubMedSource.source_type,
                prefilled=_research_facts(profile, published),
            )
        )
    return out


class PubMedSource:
    name = "pubmed"
    source_type = "pubmed"
    description = "Peer-reviewed biomedical research from PubMed. Only useful for healthcare/life-science industries."
    config_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "PubMed boolean query"}},
        "required": ["query"],
    }
    default_lookback_days = 7

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        cfg = profile.sources.get(self.name, {})
        if not cfg.get("query"):
            logger.warning(f"[{profile.id}] pubmed source has no query; skipping")
            return []
        common = {"db": "pubmed", "retmode": "json", "tool": "industry_radar"}
        if NCBI_API_KEY:
            common["api_key"] = NCBI_API_KEY
        search = http.get(ESEARCH_URL, params={
            **common,
            "term": cfg["query"],
            "datetype": "pdat",
            "mindate": since.strftime("%Y/%m/%d"),
            "maxdate": date.today().strftime("%Y/%m/%d"),
            "retmax": int(cfg.get("max_results", 15)),
            "sort": cfg.get("sort", "relevance"),
        }).json()
        ids = search.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        summaries = http.get(ESUMMARY_URL, params={**common, "id": ",".join(ids)}).json()
        return parse_pubmed_summaries(summaries, profile)


# ── arXiv ─────────────────────────────────────────────────────────────────────

ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def parse_arxiv_feed(xml_text: str, profile: IndustryProfile, since: date) -> list[RawResult]:
    root = ET.fromstring(xml_text)
    out: list[RawResult] = []
    for entry in root.findall("a:entry", _ATOM):
        published = (entry.findtext("a:published", default="", namespaces=_ATOM) or "")[:10]
        if published and published < since.isoformat():
            continue
        title = " ".join((entry.findtext("a:title", default="", namespaces=_ATOM) or "").split())
        abstract = " ".join((entry.findtext("a:summary", default="", namespaces=_ATOM) or "").split())
        url = entry.findtext("a:id", default="", namespaces=_ATOM).replace("http://", "https://")
        authors = [a.findtext("a:name", default="", namespaces=_ATOM) for a in entry.findall("a:author", _ATOM)]
        if not title or not url:
            continue
        out.append(
            RawResult(
                title=title,
                url=url,
                snippet=f"arXiv preprint by {', '.join(authors[:4])}{' et al.' if len(authors) > 4 else ''}. "
                        f"Abstract: {abstract[:1500]}",
                source="arxiv.org",
                date=published or None,
                query_tag=f"{profile.coerce_event_type('research')}:other",
                source_type=ArxivSource.source_type,
                prefilled=_research_facts(profile, published or None),
            )
        )
    return out


class ArxivSource:
    name = "arxiv"
    source_type = "arxiv"
    description = "Research preprints from arXiv (AI, robotics, physics, CS). Useful for deep-tech industries."
    config_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": 'arXiv search_query, e.g. cat:cs.RO AND abs:humanoid'}
        },
        "required": ["query"],
    }
    default_lookback_days = 7

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        cfg = profile.sources.get(self.name, {})
        if not cfg.get("query"):
            logger.warning(f"[{profile.id}] arxiv source has no query; skipping")
            return []
        response = http.get(ARXIV_URL, params={
            "search_query": cfg["query"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": int(cfg.get("max_results", 15)),
        })
        return parse_arxiv_feed(response.text, profile, since)


# ── ClinicalTrials.gov ────────────────────────────────────────────────────────

CT_URL = "https://clinicaltrials.gov/api/v2/studies"
CT_FIELDS = "NCTId,BriefTitle,OverallStatus,LeadSponsorName,LeadSponsorClass,StudyFirstPostDate,BriefSummary,Phase"


def parse_trials(data: dict, profile: IndustryProfile) -> list[RawResult]:
    out: list[RawResult] = []
    for study in data.get("studies", []):
        p = study.get("protocolSection", {})
        nct = p.get("identificationModule", {}).get("nctId")
        title = p.get("identificationModule", {}).get("briefTitle", "")
        if not nct:
            continue
        status = p.get("statusModule", {})
        posted = status.get("studyFirstPostDateStruct", {}).get("date")
        lead = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
        sponsor = lead.get("name")
        phases = ", ".join(p.get("designModule", {}).get("phases", []) or []) or "n/a"
        summary = " ".join((p.get("descriptionModule", {}).get("briefSummary") or "").split())
        facts = _research_facts(profile, posted)
        if sponsor and lead.get("class") == "INDUSTRY":
            # Only company sponsors; universities and hospitals aren't companies to track
            facts["company"] = sponsor
        out.append(
            RawResult(
                title=f"New clinical trial {nct}: {title}",
                url=f"https://clinicaltrials.gov/study/{nct}",
                snippet=f"Registered {posted}. Sponsor: {sponsor} ({lead.get('class', 'unknown')}). Status: {status.get('overallStatus')}. "
                        f"Phase: {phases}. Summary: {summary[:1200]}",
                source="clinicaltrials.gov",
                date=posted,
                query_tag=f"{facts['event_type']}:other",
                source_type=ClinicalTrialsSource.source_type,
                prefilled=facts,
            )
        )
    return out


class ClinicalTrialsSource:
    name = "clinicaltrials"
    source_type = "clinicaltrials"
    description = "Newly registered clinical trials from ClinicalTrials.gov. Only useful for healthcare industries."
    config_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Free-text trial search, e.g. robotic surgery"}},
        "required": ["query"],
    }
    default_lookback_days = 14

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        cfg = profile.sources.get(self.name, {})
        if not cfg.get("query"):
            logger.warning(f"[{profile.id}] clinicaltrials source has no query; skipping")
            return []
        data = http.get(CT_URL, params={
            "query.term": cfg["query"],
            "filter.advanced": f"AREA[StudyFirstPostDate]RANGE[{since.isoformat()},MAX]",
            "pageSize": int(cfg.get("max_results", 15)),
            "fields": CT_FIELDS,
        }).json()
        return parse_trials(data, profile)
