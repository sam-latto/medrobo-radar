"""Which websites the app trusts. Results linking anywhere else are never stored or shown.

Two tiers, both trusted:
- primary: official records, research databases, and the wires companies publish their own news on
- press:   established news organizations and trade publications with editorial standards

Government and university sites (.gov, .edu, .ac.uk) and company newsrooms on their own
domain (news.medtronic.com, newsroom.intuitive.com) count as primary. Industry profiles can
add their own trade outlets under `trusted_domains`.
"""
from urllib.parse import urlparse

PRIMARY_DOMAINS = [
    # Official records and research databases
    "fda.gov", "sec.gov", "clinicaltrials.gov", "nih.gov", "arxiv.org", "europa.eu", "who.int",
    # Wires companies use to publish their own announcements
    "businesswire.com", "prnewswire.com", "globenewswire.com",
]

PRESS_DOMAINS = [
    # General and business news
    "reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com", "nytimes.com",
    "washingtonpost.com", "economist.com", "cnbc.com", "axios.com", "bbc.com", "bbc.co.uk",
    "theguardian.com", "nikkei.com", "asia.nikkei.com", "scmp.com",
    # Technology
    "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com", "venturebeat.com",
    "technologyreview.com", "spectrum.ieee.org", "theinformation.com",
    # Health, biotech and medtech trade press
    "statnews.com", "fiercebiotech.com", "fiercehealthcare.com", "fiercepharma.com",
    "medtechdive.com", "massdevice.com", "medicaldevice-network.com", "endpts.com",
    "mobihealthnews.com", "healthcareitnews.com", "beckershospitalreview.com", "modernhealthcare.com",
    "mddionline.com",
    # Robotics and automation trade press
    "therobotreport.com", "roboticsbusinessreview.com", "automationworld.com",
    "roboticstomorrow.com", "assemblymag.com", "automate.org",
    # Peer-reviewed journals
    "nature.com", "science.org", "nejm.org", "thelancet.com", "jamanetwork.com", "bmj.com",
    "cell.com", "springer.com", "link.springer.com", "wiley.com", "sciencedirect.com",
]

INSTITUTIONAL_SUFFIXES = (".gov", ".edu", ".ac.uk", ".gov.uk", ".edu.au")
NEWSROOM_PREFIXES = ("news.", "newsroom.", "press.", "investors.", "investor.", "ir.")

# Sources whose results come from official APIs, not arbitrary web pages
TRUSTED_SOURCE_TYPES = {"openfda", "sec_form_d", "pubmed", "arxiv", "clinicaltrials"}


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def trust_level(url: str, extra_domains: list[str] | None = None) -> str | None:
    """'primary', 'press', or None when the site isn't trusted."""
    host = _host(url)
    if not host:
        return None
    if (_matches(host, PRIMARY_DOMAINS) or host.endswith(INSTITUTIONAL_SUFFIXES)
            or host.startswith(NEWSROOM_PREFIXES)):
        return "primary"
    if _matches(host, PRESS_DOMAINS + list(extra_domains or [])):
        return "press"
    return None


def is_trusted(url: str, source_type: str | None = None, extra_domains: list[str] | None = None) -> bool:
    if source_type in TRUSTED_SOURCE_TYPES:
        return True
    return trust_level(url, extra_domains) is not None


def search_domains(extra_domains: list[str] | None = None) -> list[str]:
    """Every trusted site, for restricting web search to them."""
    return list(dict.fromkeys(PRIMARY_DOMAINS + PRESS_DOMAINS + list(extra_domains or [])))
