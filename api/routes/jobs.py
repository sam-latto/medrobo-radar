"""Jobs: resumes, preferences, metered find-and-score and resume tailoring, matches."""
import logging
import uuid
from pathlib import Path
from typing import Annotated

import psycopg

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field, StringConstraints

from api import limits
from api.auth import CurrentUser, current_user
from api.db import as_service, as_user
from api.deps import get_conn
from api.errors import ApiError, not_found
from api.stores import PgJobsStore
from api.tasks import run_task, start_metered_task
from jobs import matcher
from jobs.resume import MAX_RESUME_CHARS, ResumeParseError, extract_text

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])

MAX_RESUMES = 5
MAX_ROLE_TYPES = 20
MAX_CUSTOM_ROLE_TYPES = 10
RoleType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


# ── Resumes ──────────────────────────────────────────────────────────────────

RESUME_FIELDS = "id, filename, role_types, created_at, length(text) as characters"


@router.get("/resumes")
def list_resumes(user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        rows = conn.execute(f"select {RESUME_FIELDS} from public.resumes where user_id = %s "
                            "order by created_at desc", (user.id,)).fetchall()
    return {"items": rows}


@router.post("/resumes", status_code=201)
def upload_resume(request: Request, file: UploadFile = File(...),
                  role_types: list[RoleType] = Form(default=[], max_length=MAX_ROLE_TYPES),
                  user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    filename = (file.filename or "")[:255]
    suffix = Path(filename).suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise ApiError(422, "unsupported_file", "Upload a PDF, Word (.docx) or text file.")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(422, "file_too_large", "Resumes must be 5 MB or smaller.")
    try:  # the file type is already checked, so a parse error means too little readable text
        text = extract_text(filename, data)[:MAX_RESUME_CHARS]
    except ResumeParseError as e:
        raise ApiError(422, "unreadable_file", str(e))
    except Exception:
        raise ApiError(422, "unreadable_file", "We couldn't read that file. Try saving it as a PDF again.")
    too_many = ApiError(422, "too_many_resumes", f"You can keep up to {MAX_RESUMES} resumes. Delete one first.")
    with as_user(conn, user.id):
        count = conn.execute("select count(*) as n from public.resumes where user_id = %s",
                             (user.id,)).fetchone()["n"]
    if count >= MAX_RESUMES:
        raise too_many

    path = f"{user.id}/{uuid.uuid4()}{suffix}"
    request.app.state.admin.upload_resume(path, data, CONTENT_TYPES[suffix])
    try:
        with as_user(conn, user.id):
            row = conn.execute(
                f"insert into public.resumes (user_id, filename, storage_path, text, role_types) "
                f"values (%s, %s, %s, %s, %s) returning {RESUME_FIELDS}",
                (user.id, filename, path, text, role_types)).fetchone()
    except psycopg.errors.CheckViolation:  # two uploads at once: the database's row limit caught it
        request.app.state.admin.delete_files([path])
        raise too_many
    except Exception:
        request.app.state.admin.delete_files([path])
        raise
    return row


class ResumeUpdate(BaseModel):
    role_types: list[RoleType] = Field(max_length=MAX_ROLE_TYPES)


@router.patch("/resumes/{resume_id}")
def update_resume(resume_id: uuid.UUID, body: ResumeUpdate, user: CurrentUser = Depends(current_user),
                  conn=Depends(get_conn)):
    with as_user(conn, user.id):
        row = conn.execute(f"update public.resumes set role_types = %s where id = %s and user_id = %s "
                           f"returning {RESUME_FIELDS}", (body.role_types, resume_id, user.id)).fetchone()
    if row is None:
        raise not_found("Resume not found.")
    return row


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(request: Request, resume_id: uuid.UUID, user: CurrentUser = Depends(current_user),
                  conn=Depends(get_conn)):
    with as_user(conn, user.id):
        row = conn.execute("delete from public.resumes where id = %s and user_id = %s returning storage_path",
                           (resume_id, user.id)).fetchone()
    if row is None:
        raise not_found("Resume not found.")
    # Only ever delete inside the user's own folder (the secret key bypasses storage rules)
    if row["storage_path"] and row["storage_path"].startswith(f"{user.id}/"):
        try:
            request.app.state.admin.delete_files([row["storage_path"]])
        except Exception:
            logger.exception("Resume file left in storage after its row was deleted")


# ── Preferences ──────────────────────────────────────────────────────────────

class JobPreferences(BaseModel):
    role_types: list[RoleType] = Field(default_factory=list, max_length=MAX_ROLE_TYPES)
    custom_role_types: str = Field(default="", max_length=300)
    locations: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]] = Field(
        default_factory=list, max_length=20)
    include_remote: bool = True
    remote_only: bool = False
    seniority: list[Annotated[str, StringConstraints(max_length=50)]] = Field(default_factory=list, max_length=10)
    industry_ids: list[Annotated[str, StringConstraints(max_length=64)]] = Field(default_factory=list, max_length=25)


def _preferences(conn, user_id) -> dict:
    row = conn.execute("select * from public.job_preferences where user_id = %s", (user_id,)).fetchone()
    return JobPreferences(**{k: v for k, v in (row or {}).items() if k in JobPreferences.model_fields}).model_dump()


@router.get("/job-preferences")
def get_preferences(user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        return _preferences(conn, user.id)


@router.put("/job-preferences")
def put_preferences(body: JobPreferences, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    values = body.model_dump()
    cols = ", ".join(values)
    with as_user(conn, user.id):
        conn.execute(
            f"insert into public.job_preferences (user_id, {cols}) values (%(user_id)s, "
            + ", ".join(f"%({k})s" for k in values) + ") on conflict (user_id) do update set "
            + ", ".join(f"{k} = excluded.{k}" for k in values) + ", updated_at = now()",
            {**values, "user_id": user.id})
        return _preferences(conn, user.id)


# ── Find & score ─────────────────────────────────────────────────────────────

def all_role_types(prefs: dict) -> list[str]:
    """Chosen role types plus up to MAX_CUSTOM_ROLE_TYPES typed-in ones (each a search cost)."""
    custom = [r.strip()[:100] for r in prefs["custom_role_types"].split(",") if r.strip()][:MAX_CUSTOM_ROLE_TYPES]
    return list(dict.fromkeys(prefs["role_types"] + custom))


def resume_assignments(resumes: list[dict], role_types: list[str]) -> list[tuple[dict, list[str]]]:
    """Which role types each resume is scored for. Resumes mapped to role types get those;
    unmapped role types go to the newest resume."""
    pairs, covered = [], set()
    for r in resumes:
        mine = [t for t in r["role_types"] if t in role_types]
        if mine:
            pairs.append((r, mine))
            covered.update(mine)
    rest = [t for t in role_types if t not in covered]
    if rest and resumes:
        newest = resumes[0]
        for i, (r, types) in enumerate(pairs):
            if r["id"] == newest["id"]:
                pairs[i] = (r, types + rest)
                break
        else:
            pairs.append((newest, rest))
    return pairs


def _active_postings(conn, industry_ids: list[str]) -> list[dict]:
    return conn.execute(
        """select distinct p.id, p.url, p.provider, p.board_token, p.company, p.title, p.location, p.remote,
                  p.department, p.employment_type, p.compensation, p.description, p.posted_at
           from public.job_postings p join public.job_posting_industries pi on pi.job_id = p.id
           where p.active and pi.industry_id = any(%s)""", (industry_ids,)).fetchall()


def find_and_score(user_id, prefs: dict, resumes: list[dict], industry_ids: list[str], industry_names: list[str]):
    def work(conn, progress) -> dict:
        role_types = all_role_types(prefs)
        progress("Finding matching roles…")
        with as_service(conn):
            postings = _active_postings(conn, industry_ids)
        store = PgJobsStore(conn, user_id)
        scored = candidates = 0
        for resume, types in resume_assignments(resumes, role_types):
            jobs = matcher.candidate_jobs(industry_ids, types, prefs["locations"], prefs["include_remote"],
                                          prefs["remote_only"], postings=postings)
            candidates += len(jobs)
            scored += matcher.score_jobs(resume, jobs, industry_names, progress=progress, store=store)
        return {"candidates": candidates, "scored": scored}
    return work


@router.post("/jobs/search", status_code=202)
def start_search(request: Request, background: BackgroundTasks, user: CurrentUser = Depends(current_user),
                 conn=Depends(get_conn)):
    with as_user(conn, user.id):
        prefs = _preferences(conn, user.id)
        resumes = conn.execute("select id, text, role_types from public.resumes where user_id = %s "
                               "order by created_at desc limit %s", (user.id, MAX_RESUMES)).fetchall()
        industry_ids = prefs["industry_ids"] or [r["industry_id"] for r in conn.execute(
            "select industry_id from public.subscriptions where user_id = %s", (user.id,))]
        names = [r["name"] for r in conn.execute("select name from public.industries where id = any(%s)",
                                                 (industry_ids,))]
    if not resumes:
        raise ApiError(422, "no_resume", "Upload a resume first.")
    if not all_role_types(prefs):
        raise ApiError(422, "no_role_types", "Choose at least one role type in your job preferences.")
    if not industry_ids:
        raise ApiError(422, "no_industries", "Choose at least one industry first.")

    task_id, usage_id, is_new = start_metered_task(conn, user.id, "find_jobs", limits.FIND_JOBS, "find_jobs")
    if is_new:
        background.add_task(run_task, request.app.state.db, task_id, user.id, usage_id,
                            find_and_score(user.id, prefs, resumes, industry_ids, names))
    return {"task_id": task_id, "status": "queued"}


@router.get("/tasks/{task_id}")
def get_task(task_id: uuid.UUID, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        row = conn.execute("select id, kind, status, progress, result, error, created_at, updated_at "
                           "from public.tasks where id = %s", (task_id,)).fetchone()
    if row is None:
        raise not_found("Task not found.")
    return row


# ── Matches ──────────────────────────────────────────────────────────────────

@router.get("/jobs/matches")
def list_matches(min_score: int = Query(default=0, ge=0, le=100), q: str | None = None,
                 location: str | None = None, industry_id: str | None = None,
                 resume_id: uuid.UUID | None = None, include_closed: bool = False,
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                 user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    clauses = ["m.user_id = %(user)s", "m.score >= %(min)s"]
    params: dict = {"user": user.id, "min": min_score}
    if not include_closed:
        clauses.append("p.active")
    if q and q.strip():
        clauses.append("(p.title ilike %(q)s or p.company ilike %(q)s)")
        params["q"] = f"%{q.strip()}%"
    if location and location.strip():
        clauses.append("p.location ilike %(loc)s")
        params["loc"] = f"%{location.strip()}%"
    if industry_id:
        clauses.append("exists (select 1 from public.job_posting_industries pi "
                       "where pi.job_id = p.id and pi.industry_id = %(industry)s)")
        params["industry"] = industry_id
    if resume_id:
        clauses.append("m.resume_id = %(resume)s")
        params["resume"] = resume_id
    where = " and ".join(clauses)
    with as_user(conn, user.id):
        total = conn.execute(f"select count(*) as n from public.job_matches m join public.job_postings p "
                             f"on p.id = m.job_id where {where}", params).fetchone()["n"]
        rows = conn.execute(
            f"""select m.job_id, m.resume_id, m.score, m.summary, m.strengths, m.gaps, m.industry_match,
                       m.created_at as scored_at, p.title, p.company, p.location, p.remote, p.url,
                       p.compensation, p.description, p.posted_at, p.active,
                       exists (select 1 from public.resume_advice a where a.user_id = m.user_id
                               and a.resume_id = m.resume_id and a.kind = 'job'
                               and a.key = m.job_id::text) as tailored
                from public.job_matches m join public.job_postings p on p.id = m.job_id
                where {where} order by m.score desc, p.posted_at desc nulls last
                limit %(limit)s offset %(offset)s""",
            {**params, "limit": page_size, "offset": (page - 1) * page_size}).fetchall()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


# ── Tailoring ────────────────────────────────────────────────────────────────

class TailorRequest(BaseModel):
    resume_id: uuid.UUID | None = None


def _pick_resume(conn, user_id, job_id: int, resume_id) -> dict | None:
    """The requested resume, else the one that scored best for this job, else the newest."""
    if resume_id:
        return conn.execute("select id, text from public.resumes where id = %s and user_id = %s",
                            (resume_id, user_id)).fetchone()
    return conn.execute(
        """select r.id, r.text from public.resumes r
           left join public.job_matches m on m.resume_id = r.id and m.job_id = %s and m.user_id = r.user_id
           where r.user_id = %s order by m.score desc nulls last, r.created_at desc limit 1""",
        (job_id, user_id)).fetchone()


@router.post("/jobs/{job_id}/tailor", status_code=202)
def start_tailoring(job_id: int, request: Request, background: BackgroundTasks,
                    body: TailorRequest | None = None, user: CurrentUser = Depends(current_user),
                    conn=Depends(get_conn)):
    with as_user(conn, user.id):
        job = conn.execute("select * from public.job_postings where id = %s", (job_id,)).fetchone()
        if job is None:
            raise not_found("Job not found.")
        resume = _pick_resume(conn, user.id, job_id, body.resume_id if body else None)
        if resume is None:
            raise ApiError(422, "no_resume", "Upload a resume first.")
        cached = conn.execute(
            "select content from public.resume_advice where user_id = %s and resume_id = %s "
            "and kind = 'job' and key = %s", (user.id, resume["id"], str(job_id))).fetchone()
    if cached:  # already tailored: free
        return {"status": "complete", "resume_id": resume["id"], "advice": cached["content"]}

    task_id, usage_id, is_new = start_metered_task(
        conn, user.id, "tailor_resume", limits.TAILOR_RESUME, f"tailor:{job_id}:{resume['id']}")
    if not is_new:  # a double click: the same tailoring is already running
        return {"status": "queued", "task_id": task_id, "resume_id": resume["id"]}

    def work(task_conn, progress) -> dict:
        progress("Reading the posting and your resume…")
        advice = matcher.tailor_resume(resume, job, store=PgJobsStore(task_conn, user.id))
        return {"job_id": job_id, "resume_id": str(resume["id"]), "advice": advice}

    background.add_task(run_task, request.app.state.db, task_id, user.id, usage_id, work)
    return {"status": "queued", "task_id": task_id, "resume_id": resume["id"]}


@router.get("/jobs/{job_id}/tailor")
def get_tailoring(job_id: int, resume_id: uuid.UUID | None = None, user: CurrentUser = Depends(current_user),
                  conn=Depends(get_conn)):
    with as_user(conn, user.id):
        row = conn.execute(
            """select resume_id, content, created_at from public.resume_advice
               where user_id = %s and kind = 'job' and key = %s and (%s::uuid is null or resume_id = %s)
               order by created_at desc limit 1""",
            (user.id, str(job_id), resume_id, resume_id)).fetchone()
    if row is None:
        raise not_found("No tailored suggestions for this job yet.")
    return {"status": "complete", "resume_id": row["resume_id"], "advice": row["content"],
            "created_at": row["created_at"]}
