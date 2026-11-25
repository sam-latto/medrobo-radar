"""Match postings to a resume and suggest resume changes, using Claude."""
import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from database import jobs_db
from jobs.functions import keyword_matchers, matching_functions
from jobs.resume import MAX_RESUME_CHARS
from pipeline.llm import call_tool

logger = logging.getLogger(__name__)

SCORE_BATCH_SIZE = 8
MAX_SCORED_PER_SEARCH = 40
SCORING_DESCRIPTION_CHARS = 3000
TAILORING_DESCRIPTION_CHARS = 12_000

HONESTY_RULES = (
    "Never invent employers, titles, dates, degrees, certifications, skills or results that the resume "
    "does not support. You may reword, reorder, and reframe real experience in the job's language. If a "
    "suggestion would benefit from a number the resume doesn't give, use a placeholder like [X%] for the "
    "candidate to fill in truthfully."
)


def _resume_system(resume_text: str, role: str) -> list[dict]:
    # The resume is identical across calls, so cache it as the prompt prefix
    return [{
        "type": "text",
        "text": f"{role}\n\n<resume>\n{resume_text[:MAX_RESUME_CHARS]}\n</resume>",
        "cache_control": {"type": "ephemeral"},
    }]


# ── Filtering ─────────────────────────────────────────────────────────────────

def _term_re(term: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(term.strip().lower())}(?![a-z0-9])")


def is_remote(job: dict) -> bool:
    return job.get("remote") == 1 or "remote" in (job.get("location") or "").lower()


def location_ok(job: dict, locations: list[str], include_remote: bool, remote_only: bool) -> bool:
    if remote_only:
        return is_remote(job)
    if include_remote and is_remote(job):
        return True
    terms = [t for t in locations if t.strip()]
    if not terms:
        return True
    loc = (job.get("location") or "").lower()
    if not loc and job.get("provider") == "web":
        return True  # location unknown for some web results; let the user judge
    return any(_term_re(t).search(loc) for t in terms)


def candidate_jobs(industry_ids: list[str], functions: list[str], locations: list[str],
                   include_remote: bool = True, remote_only: bool = False,
                   postings: list[dict] | None = None) -> list[dict]:
    """Active postings in the industries whose title matches a target function and location.

    `postings` defaults to the local database's active postings for `industry_ids`.
    """
    out = []
    seen: set[tuple] = set()
    if postings is None:
        postings = jobs_db.list_active_postings(industry_ids)
    matchers = keyword_matchers(functions)
    # Board postings first, so they win over web-search copies of the same job
    postings = sorted(postings, key=lambda j: j["provider"] == "web")
    for job in postings:
        fns = matching_functions(job["title"], functions, matchers)
        if not fns or not location_ok(job, locations, include_remote, remote_only):
            continue
        key = ((job.get("company") or "").lower(), job["title"].lower(), (job.get("location") or "").lower())
        if key in seen:  # the same posting found on a board and via search
            continue
        seen.add(key)
        out.append({**job, "functions": fns})
    out.sort(key=lambda j: j.get("posted_at") or "", reverse=True)
    return out


# ── Scoring ───────────────────────────────────────────────────────────────────

SCORE_TOOL = {
    "name": "score_jobs",
    "description": "Record how well the candidate fits each job.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "integer"},
                        "score": {"type": "integer", "description": "0-100 fit score"},
                        "summary": {"type": "string", "description": "One or two sentences on overall fit"},
                        "strengths": {"type": "array", "items": {"type": "string"},
                                      "description": "Up to 3 reasons the candidate fits"},
                        "gaps": {"type": "array", "items": {"type": "string"},
                                 "description": "Up to 3 missing requirements or concerns"},
                        "level_fit": {"type": "string", "enum": ["below", "match", "above"],
                                      "description": "Job seniority relative to the candidate's level"},
                        "company": {"type": "string",
                                    "description": "Hiring company's name as stated in the posting"},
                        "industry_match": {"type": "boolean",
                                           "description": "false if the employer/role is outside the target industries"},
                    },
                    "required": ["job_id", "score", "summary", "strengths", "gaps", "level_fit",
                                 "industry_match"],
                },
            }
        },
        "required": ["scores"],
    },
}

SCORING_ROLE = (
    "You are an experienced recruiter and career coach assessing job fit for the candidate whose resume "
    "follows. Calibrate scores: 85-100 strong fit (likely interview), 70-84 good fit, 50-69 stretch, "
    "below 50 poor fit. Weigh the job function, seniority, domain/industry experience, and must-have "
    "qualifications. Be candid; do not inflate."
)


def _job_block(job: dict, max_chars: int) -> str:
    return (
        f"[job_id={job['id']}]\nTitle: {job['title']}\nCompany: {job.get('company') or 'unknown'}\n"
        f"Location: {job.get('location') or 'unknown'}{' (remote)' if is_remote(job) else ''}\n"
        f"Description:\n{(job.get('description') or '(no description available)')[:max_chars]}"
    )


_PLACEHOLDER_COMPANIES = {"", "unknown", "n/a", "na", "not stated", "not specified", "confidential"}
SCORING_WORKERS = 3


def _score_batch(batch: list[dict], system: list[dict], target_industries: list[str]) -> dict:
    prompt = (
        f"The candidate is targeting roles in: {', '.join(target_industries)}. Set industry_match=false "
        "when the employer isn't in these industries (e.g. a bank or consumer-goods company hiring a "
        "generic strategy role), and score such roles lower. Write summaries about the candidate and the "
        "role; don't mention job_id numbers.\n\nScore the candidate's fit for each job:\n\n"
        + "\n\n---\n\n".join(_job_block(j, SCORING_DESCRIPTION_CHARS) for j in batch)
    )
    return call_tool(prompt, SCORE_TOOL, max_tokens=16000, system=system)


def score_jobs(resume: dict, jobs: list[dict], target_industries: list[str],
               progress: Callable[[str], None] | None = None, store=jobs_db) -> int:
    """Score jobs not yet scored for this resume (up to MAX_SCORED_PER_SEARCH). Returns number scored.

    Postings with full descriptions from job boards are scored before web-search snippets.
    `store` provides get_matches / save_match / update_posting_company (default: local database).
    """
    progress = progress or (lambda msg: None)
    done = store.get_matches(resume["id"])
    todo = sorted((j for j in jobs if j["id"] not in done),
                  key=lambda j: j["provider"] == "web")[:MAX_SCORED_PER_SEARCH]
    if not todo:
        return 0
    system = _resume_system(resume["text"], SCORING_ROLE)
    batches = [todo[i:i + SCORE_BATCH_SIZE] for i in range(0, len(todo), SCORE_BATCH_SIZE)]
    progress(f"Scoring {len(todo)} jobs against your resume…")
    scored = failed_batches = 0
    with ThreadPoolExecutor(max_workers=SCORING_WORKERS) as pool:
        futures = {pool.submit(contextvars.copy_context().run, _score_batch, b, system, target_industries): b
                   for b in batches}
        for future in as_completed(futures):
            by_id = {j["id"]: j for j in futures[future]}
            try:
                data = future.result()
            except Exception as e:
                logger.error(f"Job scoring batch failed: {e}")
                failed_batches += 1
                continue
            for s in data.get("scores", []):
                job = by_id.get(s.get("job_id"))
                if not job:
                    continue
                score = max(0, min(100, int(s.get("score", 0))))
                try:  # one bad row (e.g. the resume was deleted meanwhile) shouldn't lose the rest
                    store.save_match(job["id"], resume["id"], score, s.get("summary", ""),
                                     s.get("strengths", [])[:3], s.get("gaps", [])[:3], s.get("level_fit"),
                                     s.get("industry_match"))
                    company = (s.get("company") or "").strip()
                    if company.lower() not in _PLACEHOLDER_COMPANIES and not job.get("company"):
                        store.update_posting_company(job["id"], company)
                except Exception as e:
                    logger.error(f"Could not save a job match: {e}")
                    continue
                scored += 1
            progress(f"Scored {scored}/{len(todo)} jobs")
    if failed_batches == len(batches):
        raise RuntimeError("every scoring batch failed")
    return scored


# ── Advice ────────────────────────────────────────────────────────────────────

OVERALL_TOOL = {
    "name": "resume_recommendations",
    "description": "Overall resume recommendations for the candidate's target roles.",
    "input_schema": {
        "type": "object",
        "properties": {
            "positioning": {"type": "string",
                            "description": "One or two sentences on how the candidate should position themselves"},
            "summary_rewrite": {"type": "string", "description": "A suggested resume summary/headline"},
            "top_changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "change": {"type": "string"},
                        "why": {"type": "string"},
                        "example": {"type": "string", "description": "Concrete before/after or wording"},
                    },
                    "required": ["change", "why"],
                },
                "description": "3-6 highest-impact changes, most important first",
            },
            "skills_to_highlight": {"type": "array", "items": {"type": "string"}},
            "gaps_to_close": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"gap": {"type": "string"}, "how": {"type": "string"}},
                          "required": ["gap", "how"]},
            },
            "best_fit_roles": {"type": "array", "items": {"type": "string"},
                               "description": "Job titles the candidate is most competitive for"},
        },
        "required": ["positioning", "summary_rewrite", "top_changes", "skills_to_highlight",
                     "gaps_to_close", "best_fit_roles"],
    },
}

TAILOR_TOOL = {
    "name": "tailor_resume",
    "description": "Resume changes that would make the candidate stronger for one specific job.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fit_overview": {"type": "string", "description": "2-3 sentences: fit and the main thing to fix"},
            "summary_rewrite": {"type": "string", "description": "Resume summary tailored to this job"},
            "bullet_rewrites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "original": {"type": "string", "description": "A bullet copied exactly from the resume"},
                        "suggested": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["original", "suggested", "why"],
                },
                "description": "3-6 rewrites of existing bullets, highest impact first",
            },
            "keywords_to_add": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"keyword": {"type": "string"},
                                         "where": {"type": "string", "description": "Where it truthfully fits"}},
                          "required": ["keyword", "where"]},
                "description": "Terms from the posting the resume should include (only where true)",
            },
            "emphasize": {"type": "array", "items": {"type": "string"},
                          "description": "Experience to move up or expand"},
            "de_emphasize": {"type": "array", "items": {"type": "string"},
                             "description": "Content to shorten or cut for this job"},
            "gaps": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"gap": {"type": "string"}, "how_to_address": {"type": "string"}},
                          "required": ["gap", "how_to_address"]},
            },
            "cover_letter_angle": {"type": "string", "description": "The story to lead with when applying"},
        },
        "required": ["fit_overview", "summary_rewrite", "bullet_rewrites", "keywords_to_add", "emphasize",
                     "de_emphasize", "gaps", "cover_letter_angle"],
    },
}

COACH_ROLE = (
    "You are an expert resume writer and career coach for the candidate whose resume follows. "
    + HONESTY_RULES
)


def overall_key(functions: list[str], industry_ids: list[str]) -> str:
    return "|".join(sorted(functions)) + "@" + ",".join(sorted(industry_ids))


def overall_recommendations(resume: dict, functions: list[str], industry_names: list[str],
                            industry_ids: list[str], top_matches: list[dict]) -> dict:
    key = overall_key(functions, industry_ids)
    cached = jobs_db.get_advice(resume["id"], "overall", key)
    if cached:
        return cached
    examples = "\n".join(
        f"- {m['title']} at {m.get('company') or 'unknown'} (fit {m['score']}): gaps: {'; '.join(m.get('gaps', []))}"
        for m in top_matches[:10]
    ) or "(no scored jobs yet)"
    prompt = (
        f"The candidate is targeting {', '.join(functions)} roles in {', '.join(industry_names)}.\n\n"
        f"Their best current job matches and the gaps found:\n{examples}\n\n"
        "Recommend the highest-impact changes to their resume for these target roles. Be specific to this "
        "resume: quote its wording where useful."
    )
    data = call_tool(prompt, OVERALL_TOOL, max_tokens=8000,
                     system=_resume_system(resume["text"], COACH_ROLE))
    jobs_db.save_advice(resume["id"], "overall", key, data)
    return data


def _normalize_ws(text: str) -> str:
    return re.sub(r"[\s•\-–—*]+", " ", text or "").strip().lower()


def tailor_resume(resume: dict, job: dict, store=jobs_db) -> dict:
    """Resume changes for one job; cached per resume and job in `store` (default: local database)."""
    cached = store.get_advice(resume["id"], "job", str(job["id"]))
    if cached:
        return cached
    prompt = (
        "Suggest specific resume changes that would make the candidate stronger for this job. Rewrite "
        "existing bullets (copy the original exactly), and only add keywords where the resume shows the "
        "candidate genuinely has that experience.\n\n" + _job_block(job, TAILORING_DESCRIPTION_CHARS)
    )
    data = call_tool(prompt, TAILOR_TOOL, max_tokens=16000,
                     system=_resume_system(resume["text"], COACH_ROLE))
    resume_norm = _normalize_ws(resume["text"])
    for rewrite in data.get("bullet_rewrites", []):
        # Flag rewrites whose "original" isn't actually in the resume
        rewrite["verified"] = _normalize_ws(rewrite.get("original", ""))[:80] in resume_norm
    store.save_advice(resume["id"], "job", str(job["id"]), data)
    return data
