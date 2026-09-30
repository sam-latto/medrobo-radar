"""Public job-board APIs: Greenhouse, Lever, Ashby, SmartRecruiters, and Workday career sites.

Every fetcher returns postings as dicts with the job_postings columns:
url, provider, board_token, company, title, location, remote, department,
employment_type, compensation, description (plain text), posted_at.
"""
import html
import re
from urllib.parse import parse_qs, urlparse

import requests

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


def _workday_site_answers(url: str) -> bool:
    """Whether a Workday site allows API search at all (some block it, answering 403 to everything)."""
    parts = parse_workday_url(url)
    try:
        http.post(f"{WORKDAY_API.format(**parts)}/jobs",
                  json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        return True
    except Exception:
        return False


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


# ── SmartRecruiters ───────────────────────────────────────────────────────────

SMARTRECRUITERS_POSTING = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}"
_SR_URL_RE = re.compile(r"^https?://(?:jobs|careers)\.smartrecruiters\.com/(?P<company>[^/?#]+)/(?P<id>\d+)")
# When a company renames its account, the API names the new identifier in a 400 error
_SR_MOVED_RE = re.compile(r"companyIdentifier \((?P<new>[^)]+)\) from publication")
_SR_PLACEHOLDER_NAME_RE = re.compile(r"^TBD_\d{4}_\d{2}_\d{2}_")


def parse_smartrecruiters_posting(data: dict, company: str | None = None) -> dict | None:
    """None when the posting is no longer active."""
    if not data.get("active", True):
        return None
    ident = (data.get("company") or {}).get("identifier")
    name = _SR_PLACEHOLDER_NAME_RE.sub("", (data.get("company") or {}).get("name") or "") or None
    loc = data.get("location") or {}
    location = ", ".join(p for p in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if p)
    sections = (data.get("jobAd") or {}).get("sections") or {}
    description = "\n\n".join(
        f"{s.get('title', '')}\n{html_to_text(s.get('text'))}".strip()
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
        if (s := sections.get(key)) and s.get("text")
    )
    return {
        "url": data.get("postingUrl") or SMARTRECRUITERS_POSTING.format(company=ident, id=data.get("id")),
        "provider": "smartrecruiters",
        "board_token": ident,
        "company": company or name,
        "title": (data.get("name") or "").strip(),
        "location": location or loc.get("fullLocation"),
        "remote": 1 if loc.get("remote") else 0,
        "department": (data.get("department") or {}).get("label"),
        "employment_type": (data.get("typeOfEmployment") or {}).get("label"),
        "compensation": None,
        "description": description,
        "posted_at": (data.get("releasedDate") or "")[:10] or None,
    }


def is_smartrecruiters_url(url: str) -> bool:
    return bool(_SR_URL_RE.match(url))


def fetch_smartrecruiters_posting(url: str, company: str | None = None) -> dict | None:
    """The current version of a SmartRecruiters posting (None if closed or gone).

    Follows account renames, so a stale search-engine URL resolves to the live posting.
    """
    m = _SR_URL_RE.match(url)
    if not m:
        return None
    ident, posting_id = m["company"], m["id"]
    for _ in range(2):
        try:
            data = http.get(SMARTRECRUITERS_POSTING.format(company=ident, id=posting_id)).json()
            return parse_smartrecruiters_posting(data, company)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            moved = _SR_MOVED_RE.search(getattr(e.response, "text", "") or "")
            if moved and moved["new"] != ident:
                ident = moved["new"]
                continue
            if status in (404, 410):
                return None
            raise
    return None


# ── Is this posting still open? ───────────────────────────────────────────────

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"


def check_posting(job: dict) -> tuple[str, dict | None]:
    """(status, refreshed posting or None). Status is open, closed, or unknown (couldn't tell)."""
    url, provider = job["url"], job["provider"]
    try:
        if parse_workday_url(url):
            try:
                posting = fetch_workday_posting(url, job.get("company"))
            except requests.HTTPError as e:
                if getattr(e.response, "status_code", None) == 403 and _workday_site_answers(url):
                    return CLOSED, None  # the site's API works, but this posting is no longer public
                raise
            return (OPEN, posting) if posting else (CLOSED, None)
        if _SR_URL_RE.match(url):
            posting = fetch_smartrecruiters_posting(url, job.get("company"))
            return (OPEN, posting) if posting else (CLOSED, None)
        post_id = posting_id_from_url(provider, url)
        if provider == "greenhouse" and post_id:
            http.get(f"https://boards-api.greenhouse.io/v1/boards/{job['board_token']}/jobs/{post_id}")
            return OPEN, None
        if provider == "lever" and post_id:
            http.get(f"https://api.lever.co/v0/postings/{job['board_token']}/{post_id}")
            return OPEN, None
        if provider == "ashby":
            urls = {p["url"] for p in fetch_ashby(job["board_token"], job.get("company"))}
            return (OPEN, None) if url in urls else (CLOSED, None)
        http.get(url)  # any other career site: a 404/410 means the page is gone
        return OPEN, None
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) in (404, 410):
            return CLOSED, None
        return UNKNOWN, None
    except Exception:
        return UNKNOWN, None


def posting_id_from_url(provider: str, url: str) -> str | None:
    """The board's id for one posting URL, to find it in the board's full listing."""
    parsed = urlparse(url)
    if provider == "greenhouse":
        gh_jid = parse_qs(parsed.query).get("gh_jid")
        if gh_jid:
            return gh_jid[0]
    segments = [s for s in parsed.path.split("/") if s]
    if provider == "greenhouse" and "jobs" in segments and segments.index("jobs") + 1 < len(segments):
        return segments[segments.index("jobs") + 1]
    if provider in ("lever", "ashby") and len(segments) >= 2:
        return segments[1]
    return None


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
