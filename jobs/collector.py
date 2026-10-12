"""Collect job postings for an industry from company job boards and the web."""
import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from tavily import TavilyClient

from config import TAVILY_API_KEY
from database.store import store
from jobs import boards
from jobs.functions import matching_functions
from pipeline.companies import normalize_name
from pipeline.llm import record_search
from profiles.loader import IndustryProfile

logger = logging.getLogger(__name__)

BOARD_REFRESH_HOURS = 12
TOP_MOMENTUM_COMPANIES = 20
WORKDAY_RESULTS_PER_FUNCTION = 10
WEB_RESULTS_PER_QUERY = 10

# Employer career sites only (no LinkedIn/Indeed scraping)
CAREER_DOMAINS = [
    "boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "myworkdayjobs.com", "jobs.smartrecruiters.com", "apply.workable.com", "careers.icims.com",
]

# Words that don't identify a company on their own ("Figure AI" -> board "figure")
_GENERIC_NAME_WORDS = {"ai", "robotics", "robotic", "robots", "technologies", "technology", "tech", "labs",
                       "systems", "group", "holdings", "global", "intelligence"}

Progress = Callable[[str], None]


def _core_words(normalized: str) -> list[str]:
    return [w for w in normalized.split() if w not in _GENERIC_NAME_WORDS]


def slug_candidates(company: str) -> tuple[list[str], list[str]]:
    """(full-name slugs, all slugs). Only full-name slugs are trusted on unverifiable boards."""
    words = normalize_name(company).split()
    if not words:
        return [], []
    full = ["".join(words), "-".join(words)]
    core = _core_words(" ".join(words))
    extra = ["".join(core), "-".join(core)] if core and core != words else []
    dedup = lambda xs: list(dict.fromkeys(x for x in xs if x))
    return dedup(full), dedup(full + extra)


def names_match(board_name: str, company: str) -> bool:
    a, b = normalize_name(board_name), normalize_name(company)
    if not a or not b:
        return False
    if a == b or _core_words(a) == _core_words(b):
        return True
    return (a + " ").startswith(b + " ") or (b + " ").startswith(a + " ")


def discover_boards(company: str) -> list[tuple[str, str]]:
    """Find a company's Greenhouse/Lever/Ashby board by trying slugs of its name (cached)."""
    norm = normalize_name(company)
    if not norm or store().was_board_checked(norm):
        return []
    full, candidates = slug_candidates(company)
    found: list[tuple[str, str]] = []

    for token in candidates:  # Greenhouse boards expose a name we can verify
        name = boards.greenhouse_board_name(token)
        if name and names_match(name, company):
            found.append(("greenhouse", token))
            break
    for token in full:
        try:
            if boards.fetch_lever(token, company):
                found.append(("lever", token))
                break
        except Exception:
            pass
    for token in full:
        try:
            if boards.fetch_ashby(token, company):
                found.append(("ashby", token))
                break
        except Exception:
            pass

    for provider, token in found:
        store().upsert_board(provider, token, company)
    store().mark_board_checked(norm)
    if found:
        logger.info(f"Found job boards for {company}: {found}")
    return found


def _company_for_tenant(tenant: str, company_names: set[str]) -> str | None:
    """Workday tenants are usually the company name squashed: 'bostondynamics' -> Boston Dynamics."""
    for name in company_names | {c["name"] for c in store().list_companies()}:
        if normalize_name(name).replace(" ", "") == tenant.lower():
            return name
    return None


def _web_queries(profile: IndustryProfile, functions: list[str]) -> list[str]:
    area = profile.name.lower()
    queries = []
    for f in functions:
        queries.append(f"{f} {area} job")
        queries.extend(f"{f} {s.replace('_', ' ')} job" for s in profile.segments[:2])
    return queries


def _web_posting(item: dict) -> dict:
    title = (item.get("title") or "").split(" - Myworkdayjobs.com")[0]
    for prefix in ("Job Application for ",):
        title = title.removeprefix(prefix)
    company = None
    if " at " in title:  # "Senior PM at Figure"
        title, company = title.rsplit(" at ", 1)
    return {
        "url": item["url"],
        "provider": "web",
        "board_token": None,
        "company": company,
        "title": title.strip(),
        "location": None,
        "remote": None,
        "description": (item.get("raw_content") or item.get("content") or "")[:20_000],
        "posted_at": (item.get("published_date") or "")[:10] or None,
    }


def search_web(profile: IndustryProfile, functions: list[str], company_names: set[str],
               progress: Progress) -> int:
    """Search employer career sites for the target functions; store only the postings found.

    Boards found this way are NOT imported wholesale: a generic "strategy manager" hit at an
    unrelated company would otherwise pull in its whole board. Postings are fetched through
    the board API only to get their full description.
    """
    client = TavilyClient(api_key=TAVILY_API_KEY)
    queries = _web_queries(profile, functions)
    progress(f"Searching career sites ({len(queries)} searches)…")
    def search(q):
        results = client.search(query=q, search_depth="advanced", max_results=WEB_RESULTS_PER_QUERY,
                                include_domains=CAREER_DOMAINS, include_raw_content=True).get("results", [])
        record_search("job_search", "advanced")
        return results
    items = {}
    for _, results in _parallel(search, queries, workers=4):
        for item in results or []:
            if item.get("url"):
                items.setdefault(item["url"], item)

    board_cache: dict[tuple[str, str], list[dict]] = {}
    stored = 0
    for url, item in items.items():
        board = boards.board_from_url(url)
        try:
            posting = None
            if board and board[0] in ("greenhouse", "lever", "ashby"):
                if board not in board_cache:
                    board_cache[board] = boards.fetch_board(*board)
                wanted = boards.posting_id_from_url(board[0], url)
                posting = next((p for p in board_cache[board]
                                if wanted and boards.posting_id_from_url(board[0], p["url"]) == wanted), None)
                if posting is None:
                    continue  # no longer on the board: the job has closed
                posting["company"] = posting.get("company") or board[1].replace("-", " ").title()
            elif board and board[0] == "workday":
                company = _company_for_tenant(board[1].split("|")[1], company_names)
                if company in company_names:
                    # A tracked company's Workday site: search it by function on every refresh
                    store().upsert_board("workday", board[1], company)
                try:
                    posting = boards.fetch_workday_posting(url, company)
                except Exception as e:
                    # Some Workday sites refuse API requests (403); keep the search result's text
                    logger.info(f"Workday detail unavailable for {url} ({e}); using search text")
                    posting = {**_web_posting(item), "company": company}
            elif boards.is_smartrecruiters_url(url):
                try:
                    posting = boards.fetch_smartrecruiters_posting(url)
                except Exception as e:
                    logger.info(f"SmartRecruiters detail unavailable for {url} ({e}); using search text")
                    posting = _web_posting(item)
                if posting is None:
                    continue  # closed: search engines keep indexing expired postings
            else:
                posting = _web_posting(item)
            if posting:
                store().upsert_posting(posting, profile.id)
                stored += 1
        except Exception as e:
            logger.warning(f"Could not store job result {url}: {e}")
    return stored


RECHECK_AFTER_HOURS = 24
# Postings no full-board refresh covers; each is re-checked on its own
RECHECK_PROVIDERS = ("web", "smartrecruiters", "workday")


def recheck_postings(profile: IndustryProfile, progress: Progress) -> int:
    """Re-verify older search-found postings are still open; returns how many closed."""
    stale = store().postings_to_recheck(profile.id, RECHECK_AFTER_HOURS, RECHECK_PROVIDERS)
    if not stale:
        return 0
    progress(f"Checking {len(stale)} earlier postings are still open…")
    closed = 0
    for job, result in _parallel(boards.check_posting, stale):
        status, fresh = result or (boards.UNKNOWN, None)
        if status == boards.CLOSED:
            store().mark_posting_closed(job["id"])
            closed += 1
        elif status == boards.OPEN:
            store().refresh_posting(job["id"], fresh)
    if closed:
        progress(f"Removed {closed} postings that have closed")
    return closed


def _industry_boards(companies: list[str]) -> list[dict]:
    """Boards of companies this industry tracks: the only boards read in full."""
    wanted = {normalize_name(c) for c in companies}
    return [b for b in store().list_boards() if b.get("company") and normalize_name(b["company"]) in wanted]


def _fetch_board_into_db(board: dict, profile: IndustryProfile, functions: list[str]) -> int:
    provider, token, company = board["provider"], board["token"], board.get("company")
    if provider == "workday":
        postings = []
        title_ok = lambda title: bool(matching_functions(title, functions))
        for f in functions:
            postings.extend(boards.search_workday_board(token, f, company, WORKDAY_RESULTS_PER_FUNCTION,
                                                        title_filter=title_ok))
    else:
        postings = boards.fetch_board(provider, token, company)
        for p in postings:
            p["company"] = p.get("company") or company
    for p in postings:
        store().upsert_posting(p, profile.id)
    if provider != "workday":  # Workday results are a search, not the full board
        store().deactivate_missing(provider, token, {p["url"] for p in postings})
    store().mark_board_fetched(provider, token)
    return len(postings)


def collect_jobs(profile: IndustryProfile, functions: list[str], progress: Progress | None = None,
                 force: bool = False) -> dict:
    """Refresh job postings for one industry. Returns counts for display."""
    progress = progress or (lambda msg: logger.info(msg))
    stats = {"boards_found": 0, "boards_fetched": 0, "postings": 0, "web_postings": 0, "closed": 0}
    stats["closed"] = recheck_postings(profile, progress)

    ranked = [r["company"] for r in store().rank_companies(profile) if r["score"] > 0][:TOP_MOMENTUM_COMPANIES]
    companies = list(dict.fromkeys(profile.watchlist + ranked))
    unchecked = [c for c in companies if not store().was_board_checked(normalize_name(c))]
    if unchecked:
        progress(f"Looking for job boards at {len(unchecked)} companies…")
        for company, found in _parallel(discover_boards, unchecked):
            stats["boards_found"] += len(found or [])
            if found:
                progress(f"Found {', '.join(p for p, _ in found)} board for {company}")

    stats["web_postings"] = search_web(profile, functions, set(companies), progress)

    to_fetch = [b for b in _industry_boards(companies)
                if force or not store().board_is_fresh(b, BOARD_REFRESH_HOURS)]
    if to_fetch:
        progress(f"Reading {len(to_fetch)} job boards…")
    fetch = lambda board: _fetch_board_into_db(board, profile, functions)
    for board, count in _parallel(fetch, to_fetch):
        if count is not None:
            stats["postings"] += count
            stats["boards_fetched"] += 1
            progress(f"Read {board.get('company') or board['token']} ({board['provider']}): {count} postings")
    return stats


def _parallel(fn, items, workers: int = 8):
    """Run fn over items in threads; yields (item, result or None on error) as each finishes.

    Per-host throttling in sources.http keeps each API's request rate polite; the
    parallelism is across different companies and hosts.
    """
    if not items:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(contextvars.copy_context().run, fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                yield item, future.result()
            except Exception as e:
                logger.warning(f"Job collection step failed for {item}: {e}")
                yield item, None
