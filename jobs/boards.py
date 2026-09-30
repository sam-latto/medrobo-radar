"""Public job-board APIs: Greenhouse, Lever, Ashby, and Workday career sites.

Every fetcher returns postings as dicts with the job_postings columns:
url, provider, board_token, company, title, location, remote, department,
employment_type, compensation, description (plain text), posted_at.
"""
import html
import re
from urllib.parse import urlparse

from sources import http

GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/{token}"
GREENHOUSE_JOBS = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_POSTINGS = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY_BOARD = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
WORKDAY_API = "https://{host}/wday/cxs/{tenant}/{site}"


def html_to_text(markup: str | None) -> str:
    """Job descriptions arrive as (sometimes double-escaped) HTML; keep readable text."""
    if not markup:
        return ""
    text = html.unescape(markup)
    if "&lt;" in text or "&amp;" in text:  # Greenhouse double-escapes its content
        text = html.unescape(text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n• ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _is_remote(*values) -> int | None:
    joined = " ".join(str(v) for v in values if v).lower()
    if not joined:
        return None
    return 1 if "remote" in joined else 0


# ── Greenhouse ────────────────────────────────────────────────────────────────

def greenhouse_board_name(token: str) -> str | None:
    """The company name a Greenhouse board belongs to, or None if the board doesn't exist."""
    try:
        return http.get(GREENHOUSE_BOARD.format(token=token)).json().get("name")
    except Exception:
        return None


def parse_greenhouse(data: dict, token: str) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name")
        departments = ", ".join(d.get("name", "") for d in j.get("departments", []) if d.get("name"))
        out.append({
            "url": j["absolute_url"],
            "provider": "greenhouse",
            "board_token": token,
            "company": j.get("company_name"),
            "title": j.get("title", "").strip(),
            "location": location,
            "remote": _is_remote(location),
            "department": departments or None,
            "employment_type": None,
            "compensation": None,
            "description": html_to_text(j.get("content")),
            "posted_at": (j.get("first_published") or j.get("updated_at") or "")[:10] or None,
        })
    return out


def fetch_greenhouse(token: str) -> list[dict]:
    return parse_greenhouse(http.get(GREENHOUSE_JOBS.format(token=token)).json(), token)


# ── Lever ─────────────────────────────────────────────────────────────────────

def parse_lever(data: list, token: str, company: str | None = None) -> list[dict]:
    out = []
    for j in data:
        cats = j.get("categories") or {}
        sections = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            sections.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
        sections.append(j.get("additionalPlain") or "")
        created = j.get("createdAt")
        out.append({
            "url": j["hostedUrl"],
            "provider": "lever",
            "board_token": token,
            "company": company,
            "title": (j.get("text") or "").strip(),
            "location": cats.get("location"),
            "remote": 1 if j.get("workplaceType") == "remote" else _is_remote(cats.get("location")),
            "department": cats.get("team") or cats.get("department"),
            "employment_type": cats.get("commitment"),
            "compensation": None,
            "description": "\n\n".join(s.strip() for s in sections if s and s.strip()),
            "posted_at": _epoch_ms_to_date(created),
        })
    return out


def _epoch_ms_to_date(value) -> str | None:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def fetch_lever(token: str, company: str | None = None) -> list[dict]:
    data = http.get(LEVER_POSTINGS.format(token=token)).json()
    if not isinstance(data, list):  # {"ok": false, "error": "Document not found"}
        raise LookupError(f"No Lever board '{token}'")
    return parse_lever(data, token, company)


# ── Ashby ─────────────────────────────────────────────────────────────────────

def parse_ashby(data: dict, token: str, company: str | None = None) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append({
            "url": j["jobUrl"],
            "provider": "ashby",
            "board_token": token,
            "company": company,
            "title": (j.get("title") or "").strip(),
            "location": j.get("location"),
            "remote": 1 if j.get("isRemote") or j.get("workplaceType") == "Remote" else 0,
            "department": j.get("department") or j.get("team"),
            "employment_type": j.get("employmentType"),
            "compensation": comp,
            "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            "posted_at": (j.get("publishedAt") or "")[:10] or None,
        })
    return out


def fetch_ashby(token: str, company: str | None = None) -> list[dict]:
    return parse_ashby(http.get(ASHBY_BOARD.format(token=token)).json(), token, company)


# ── Workday ───────────────────────────────────────────────────────────────────

_WORKDAY_RE = re.compile(
    r"^https?://(?P<host>(?P<tenant>[\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?"
    r"(?P<site>[^/]+)(?P<path>/job/.+)$"
)


def parse_workday_url(url: str) -> dict | None:
    m = _WORKDAY_RE.match(url.split("?")[0])
    return m.groupdict() if m else None


def parse_workday_posting(data: dict, url: str, board_token: str, company: str | None) -> dict | None:
    info = data.get("jobPostingInfo")
    if not info or info.get("posted") is False:
        return None
    return {
        "url": info.get("externalUrl") or url,
        "provider": "workday",
        "board_token": board_token,
        "company": company,
        "title": (info.get("title") or "").strip(),
        "location": info.get("location"),
        "remote": _is_remote(info.get("remoteType"), info.get("location")),
        "department": None,
        "employment_type": info.get("timeType"),
        "compensation": None,
        "description": html_to_text(info.get("jobDescription")),
        "posted_at": info.get("startDate"),
    }


def fetch_workday_posting(url: str, company: str | None = None) -> dict | None:
    parts = parse_workday_url(url)
    if not parts:
        return None
    api = WORKDAY_API.format(**parts) + parts["path"]
    token = f"{parts['host']}|{parts['tenant']}|{parts['site']}"
    return parse_workday_posting(http.get(api).json(), url, token, company)


def search_workday_board(board_token: str, search_text: str, company: str | None = None,
                         limit: int = 20, title_filter=None) -> list[dict]:
    """Search one Workday career site (the same query its search box runs).

    Workday search is fuzzy, so `title_filter(title) -> bool` drops irrelevant hits
    before their full descriptions are fetched (one request each).
    """
    host, tenant, site = board_token.split("|")
    api = WORKDAY_API.format(host=host, tenant=tenant, site=site)
    resp = http.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": limit, "offset": 0,
                                           "searchText": search_text})
    out = []
    for p in resp.json().get("jobPostings", []):
        path = p.get("externalPath")
        if not path or (title_filter and not title_filter(p.get("title") or "")):
            continue
        posting = fetch_workday_posting(f"https://{host}/{site}{path}", company)
        if posting:
            out.append(posting)
    return out


# ── URL recognition ───────────────────────────────────────────────────────────

def board_from_url(url: str) -> tuple[str, str] | None:
    """(provider, token) for a posting URL on a supported board, else None."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    segments = [s for s in parsed.path.split("/") if s]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and segments:
        if segments[0] == "embed":
            return None
        return ("greenhouse", segments[0])
    if host == "jobs.lever.co" and segments:
        return ("lever", segments[0])
    if host == "jobs.ashbyhq.com" and segments:
        return ("ashby", segments[0])
    wd = parse_workday_url(url)
    if wd:
        return ("workday", f"{wd['host']}|{wd['tenant']}|{wd['site']}")
    return None


def fetch_board(provider: str, token: str, company: str | None = None) -> list[dict]:
    if provider == "greenhouse":
        return fetch_greenhouse(token)
    if provider == "lever":
        return fetch_lever(token, company)
    if provider == "ashby":
        return fetch_ashby(token, company)
    raise ValueError(f"Board provider '{provider}' can't be listed in full")
