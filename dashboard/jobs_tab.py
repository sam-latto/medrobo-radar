"""💼 Jobs tab: find roles in the tracked industries that fit your resume, with tailored resume advice."""
import streamlit as st

from database import jobs_db
from jobs.boards import CLOSED, OPEN, UNKNOWN, check_posting
from jobs.functions import FUNCTION_KEYWORDS
from jobs.resume import ResumeParseError, clean_text, extract_text
from pipeline.text_utils import md_safe

PREFS_KEY = "job_preferences"
DEFAULT_PREFS = {"functions": ["Product Management", "Strategy"], "custom_functions": "",
                 "locations": "", "include_remote": True, "remote_only": False}


def _score_badge(score: int | None) -> str:
    if score is None:
        return "·· not scored"
    icon = "🟢" if score >= 80 else "🟡" if score >= 65 else "🟠" if score >= 50 else "⚪"
    return f"{icon} {score}"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {md_safe(i)}" for i in items if i)


# ── Resume ────────────────────────────────────────────────────────────────────

def _render_resume() -> dict | None:
    resume = jobs_db.get_latest_resume()
    replacing = st.session_state.get("replace_resume", False)
    if resume and not replacing:
        words = len(resume["text"].split())
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"📄 **{md_safe(resume['filename'] or 'Pasted resume')}** · {words:,} words · "
                    f"added {resume['created_at'][:10]}")
        if c2.button("Replace", width="stretch"):
            st.session_state.replace_resume = True
            st.rerun()
        with st.expander("View resume text"):
            st.text(resume["text"])
        return resume

    st.caption("Your resume is stored in this app's local database and sent to Claude only to score jobs "
               "and suggest changes.")
    upload_tab, paste_tab = st.tabs(["Upload file", "Paste text"])
    with upload_tab:
        file = st.file_uploader("Resume (PDF, Word .docx, or .txt)", type=["pdf", "docx", "txt", "md"])
        if file and st.button("Save resume", type="primary", key="save_uploaded_resume"):
            try:
                jobs_db.save_resume(file.name, extract_text(file.name, file.getvalue()))
                st.session_state.replace_resume = False
                st.rerun()
            except ResumeParseError as e:
                st.error(str(e))
    with paste_tab:
        pasted = st.text_area("Resume text", height=240)
        if st.button("Save resume", type="primary", key="save_pasted_resume", disabled=not pasted.strip()):
            try:
                jobs_db.save_resume(None, clean_text(pasted))
                st.session_state.replace_resume = False
                st.rerun()
            except ResumeParseError as e:
                st.error(str(e))
    if resume and st.button("Cancel"):
        st.session_state.replace_resume = False
        st.rerun()
    return None


# ── Preferences ───────────────────────────────────────────────────────────────

def _render_preferences() -> dict:
    prefs = {**DEFAULT_PREFS, **(jobs_db.get_setting(PREFS_KEY) or {})}
    with st.form("job_prefs"):
        c1, c2 = st.columns([3, 2])
        with c1:
            functions = st.multiselect(
                "Job functions", list(FUNCTION_KEYWORDS),
                default=[f for f in prefs["functions"] if f in FUNCTION_KEYWORDS],
            )
            custom = st.text_input("Other functions (comma-separated)", value=prefs["custom_functions"],
                                   placeholder="e.g. Venture Capital, Regulatory Affairs")
        with c2:
            locations = st.text_input("Locations (comma-separated)", value=prefs["locations"],
                                      placeholder="e.g. Boston, New York, CA — blank = anywhere")
            include_remote = st.checkbox("Include remote roles", value=prefs["include_remote"])
            remote_only = st.checkbox("Remote only", value=prefs["remote_only"])
        if st.form_submit_button("Save preferences"):
            prefs = {"functions": functions, "custom_functions": custom, "locations": locations,
                     "include_remote": include_remote, "remote_only": remote_only}
            jobs_db.set_setting(PREFS_KEY, prefs)
            st.rerun()
    return prefs


def _all_functions(prefs: dict) -> list[str]:
    custom = [c.strip() for c in prefs["custom_functions"].split(",") if c.strip()]
    return list(dict.fromkeys(prefs["functions"] + custom))


# ── Advice rendering ──────────────────────────────────────────────────────────

def _render_overall(advice: dict) -> None:
    st.markdown(f"**Positioning.** {md_safe(advice['positioning'])}")
    st.markdown("**Suggested summary**")
    st.info(advice["summary_rewrite"])
    st.markdown("**Top changes**")
    for i, c in enumerate(advice.get("top_changes", []), 1):
        st.markdown(f"{i}. **{md_safe(c['change'])}** — {md_safe(c['why'])}")
        if c.get("example"):
            st.caption(c["example"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Skills to highlight**")
        st.markdown(_bullets(advice.get("skills_to_highlight", [])))
        st.markdown("**Roles you're most competitive for**")
        st.markdown(_bullets(advice.get("best_fit_roles", [])))
    with c2:
        st.markdown("**Gaps to close**")
        st.markdown(_bullets([f"{g['gap']} — {g['how']}" for g in advice.get("gaps_to_close", [])]))


def _render_tailoring(t: dict) -> None:
    st.markdown(f"**How to win this one.** {md_safe(t['fit_overview'])}")
    st.markdown("**Tailored summary**")
    st.info(t["summary_rewrite"])
    if t.get("bullet_rewrites"):
        st.markdown("**Bullet rewrites**")
        for r in t["bullet_rewrites"]:
            note = "" if r.get("verified", True) else " ⚠️ *original not found verbatim in your resume*"
            st.markdown(f"~~{md_safe(r['original'])}~~{note}  \n→ **{md_safe(r['suggested'])}**  \n"
                        f"<small>{md_safe(r['why'])}</small>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        if t.get("keywords_to_add"):
            st.markdown("**Keywords to work in**")
            st.markdown(_bullets([f"**{k['keyword']}** — {k['where']}" for k in t["keywords_to_add"]]))
        if t.get("emphasize"):
            st.markdown("**Emphasize**")
            st.markdown(_bullets(t["emphasize"]))
    with c2:
        if t.get("gaps"):
            st.markdown("**Gaps and how to address them**")
            st.markdown(_bullets([f"{g['gap']} — {g['how_to_address']}" for g in t["gaps"]]))
        if t.get("de_emphasize"):
            st.markdown("**Trim for this role**")
            st.markdown(_bullets(t["de_emphasize"]))
    if t.get("cover_letter_angle"):
        st.markdown(f"**Cover letter angle.** {md_safe(t['cover_letter_angle'])}")


def _posting_status(job: dict) -> str:
    """Check (once per session) that a posting is still open before showing or tailoring it."""
    checked = st.session_state.setdefault("posting_status", {})
    if job["id"] not in checked:
        with st.spinner("Checking the posting is still open…"):
            status, fresh = check_posting(job)
        if status == CLOSED:
            jobs_db.mark_posting_closed(job["id"])
        elif status == OPEN:
            jobs_db.refresh_posting(job["id"], fresh)
        checked[job["id"]] = status
    return checked[job["id"]]


def _render_job(job: dict, match: dict | None, resume: dict) -> None:
    title = f"{_score_badge(match['score'] if match else None)} · {job['title']} — {job.get('company') or 'Unknown company'}"
    location = job.get("location") or ("Remote" if job.get("remote") == 1 else "")
    if location:
        title += f" · {location}"
    exp = st.expander(md_safe(title), key=f"job_{job['id']}", on_change="rerun")
    with exp:
        status = _posting_status(job) if exp.open else OPEN
        if status == CLOSED:
            st.warning("This posting has closed or been taken down, so it's been removed from your list.")
            return
        job = jobs_db.get_posting(job["id"]) or job  # the check may have refreshed its URL or details
        meta = [job.get("department"), job.get("employment_type"), job.get("compensation"),
                f"posted {job['posted_at']}" if job.get("posted_at") else None,
                ", ".join(job.get("functions", [])), job["provider"].title()]
        st.caption(" · ".join(m for m in meta if m))
        st.link_button("Open posting / apply ↗", job["url"])
        if status == UNKNOWN:
            st.caption("Couldn't confirm this posting is still open; the link may be out of date.")
        if match:
            level = {"below": "below your level", "match": "at your level", "above": "a step up"}.get(match.get("level_fit"), "")
            st.markdown(f"**Fit {match['score']}/100**{f' · {level}' if level else ''}. {md_safe(match['summary'])}")
            c1, c2 = st.columns(2)
            c1.markdown("**Why you fit**\n" + _bullets(match.get("strengths", [])))
            c2.markdown("**Gaps**\n" + _bullets(match.get("gaps", [])))
        st.markdown("**Job description**")
        with st.container(height=280):
            st.text(job.get("description") or "No description available.")

        if exp.open:  # generate tailored advice only when the job is opened
            st.divider()
            st.subheader("Resume changes for this job")
            try:
                with st.spinner("Tailoring your resume to this job…"):
                    from jobs.matcher import tailor_resume
                    _render_tailoring(tailor_resume(resume, jobs_db.get_posting(job["id"]) or job))
            except Exception as e:
                st.error(f"Couldn't generate suggestions: {e}")


# ── Tab ───────────────────────────────────────────────────────────────────────

def render(selected_profiles: list, industry_id: str | None, industry_name) -> None:
    st.header("Jobs")
    scope = industry_name(industry_id) if industry_id else "all tracked industries"
    st.caption(f"Roles in **{scope}** (change in the sidebar), from company career boards and career-site "
               "search, ranked by fit with your resume.")

    st.subheader("1 · Your resume")
    resume = _render_resume()
    if not resume:
        return

    st.subheader("2 · What you're looking for")
    prefs = _render_preferences()
    functions = _all_functions(prefs)
    if not functions:
        st.info("Pick at least one job function.")
        return

    industry_ids = [p.id for p in selected_profiles]
    locations = [l.strip() for l in prefs["locations"].split(",") if l.strip()]

    st.subheader("3 · Matching jobs")
    c1, c2 = st.columns([2, 3])
    find = c1.button("🔎 Find & score jobs", type="primary", width="stretch")
    refresh = c2.checkbox("Re-read job boards even if checked in the last 12 hours", value=False)
    if find:
        from jobs.collector import collect_jobs
        from jobs.matcher import candidate_jobs, score_jobs
        with st.status("Finding jobs…", expanded=True) as status:
            for profile in selected_profiles:
                stats = collect_jobs(profile, functions, progress=status.write, force=refresh)
                status.write(f"{profile.label}: read {stats['boards_fetched']} boards "
                             f"({stats['postings']} postings), {stats['web_postings']} from search, "
                             f"{stats['closed']} closed postings removed")
            candidates = candidate_jobs(industry_ids, functions, locations, prefs["include_remote"], prefs["remote_only"])
            status.write(f"{len(candidates)} postings match your functions and location")
            scored = score_jobs(resume, candidates, [p.name for p in selected_profiles], progress=status.write)
            status.update(label=f"Done — scored {scored} new jobs", state="complete", expanded=False)

    from jobs.matcher import MAX_SCORED_PER_SEARCH, candidate_jobs, overall_recommendations
    candidates = candidate_jobs(industry_ids, functions, locations, prefs["include_remote"], prefs["remote_only"])
    matches = jobs_db.get_matches(resume["id"])
    scored_jobs = sorted((j for j in candidates if j["id"] in matches),
                         key=lambda j: matches[j["id"]]["score"], reverse=True)
    unscored = [j for j in candidates if j["id"] not in matches]

    if not candidates:
        st.info("No matching jobs yet. Click **Find & score jobs** to search.")
        return

    overall = st.expander("📝 Resume recommendations for these roles", key="overall_advice", on_change="rerun")
    with overall:
        st.caption(f"Across your target roles ({', '.join(functions)}) in {scope}. Opens on demand.")
        if overall.open:
            try:
                with st.spinner("Reviewing your resume…"):
                    top = [{**j, **matches[j["id"]]} for j in scored_jobs[:10]]
                    _render_overall(overall_recommendations(
                        resume, functions, [industry_name(i) for i in industry_ids], industry_ids, top))
            except Exception as e:
                st.error(f"Couldn't generate recommendations: {e}")

    f1, f2 = st.columns([3, 2])
    min_score = f1.slider("Minimum fit score", 0, 100, 50, step=5)
    off_industry = sum(1 for j in scored_jobs if matches[j["id"]].get("industry_match") == 0)
    show_off_industry = f2.checkbox(f"Include {off_industry} roles outside these industries",
                                    value=False) if off_industry else False
    show_unscored = f2.checkbox(f"Also show {len(unscored)} unscored matches", value=False) if unscored else False
    shown = [j for j in scored_jobs if matches[j["id"]]["score"] >= min_score
             and (show_off_industry or matches[j["id"]].get("industry_match") != 0)]
    st.caption(f"{len(shown)} of {len(scored_jobs)} scored jobs shown · open a job for tailored resume changes")
    if unscored and not show_unscored:
        st.caption(f"{len(unscored)} more matching jobs aren't scored yet; each search scores up to "
                   f"{MAX_SCORED_PER_SEARCH} — click Find & score again to continue.")

    for job in shown:
        _render_job(job, matches[job["id"]], resume)
    if show_unscored:
        for job in unscored:
            _render_job(job, None, resume)
