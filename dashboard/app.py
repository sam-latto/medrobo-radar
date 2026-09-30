import sys
import os
import html
import logging
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from database.db import (
    init_db,
    count_events,
    get_all_events,
    get_notable_events,
    get_latest_run,
    get_all_runs,
)
import json
from pipeline.event_styles import SOURCE_LABELS, event_color, event_icon, event_label, source_badge_html, source_label
from profiles.loader import load_profiles, parse_profile, save_profile
from sources.registry import SOURCES

logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="Industry Radar",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# The daily scheduler lives only in main.py; the dashboard never starts one.

ALL_INDUSTRIES = "__all__"

all_profiles = load_profiles()
active_profiles = [p for p in all_profiles if p.active]
profiles_by_id = {p.id: p for p in all_profiles}


def _industry_name(industry_id: str | None) -> str:
    p = profiles_by_id.get(industry_id or "")
    return p.label if p else (industry_id or "Unknown")


def _run_pipeline_now(industry_id: str | None) -> None:
    with st.spinner("Running pipeline… this can take several minutes"):
        try:
            from pipeline.pipeline import run_pipeline
            result = run_pipeline(profile_id=industry_id, triggered_by="manual", force=True)
            if result["status"] == "complete":
                st.session_state.flash = ("success", f"Done! {result['events']} events, {result['notable']} notable")
            else:
                st.session_state.flash = ("error", f"Pipeline {result['status']}: {result.get('error', 'unknown error')}")
        except Exception as e:
            st.session_state.flash = ("error", f"Error: {e}")
    st.rerun()


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📡 Industry Radar")
    st.caption("Multi-industry market intelligence")

    industry_options = [p.id for p in active_profiles] + [ALL_INDUSTRIES]
    if "pending_industry" in st.session_state:
        st.session_state.industry_select = st.session_state.pop("pending_industry")
    if st.session_state.get("industry_select") not in industry_options:
        st.session_state.industry_select = industry_options[0]
    selected = st.selectbox(
        "Industry",
        industry_options,
        key="industry_select",
        format_func=lambda i: "All industries" if i == ALL_INDUSTRIES else _industry_name(i),
    )
    industry_id = None if selected == ALL_INDUSTRIES else selected
    selected_profiles = [profiles_by_id[industry_id]] if industry_id else active_profiles
    if industry_id:
        st.caption(profiles_by_id[industry_id].description)

    st.divider()

    if "flash" in st.session_state:
        kind, msg = st.session_state.pop("flash")
        (st.success if kind == "success" else st.error)(msg)

    latest_run = get_latest_run(industry_id)
    if latest_run:
        st.metric("Last Run", latest_run["run_date"])
        status_color = {"complete": "🟢", "running": "🟡", "failed": "🔴"}.get(
            latest_run["status"], "⚪"
        )
        run_scope = "" if industry_id else f" · {_industry_name(latest_run.get('industry_id'))}"
        st.write(f"{status_color} {latest_run['status'].capitalize()}{run_scope}")
        if latest_run.get("triggered_by"):
            st.caption(f"Triggered by: {latest_run['triggered_by']}")
    else:
        st.info("No pipeline runs yet")

    st.divider()

    today = date.today().isoformat()
    today_complete = all(
        any(r["run_date"] == today and r["status"] == "complete" for r in get_all_runs(p.id))
        for p in selected_profiles
    ) if selected_profiles else False

    if "confirm_rerun" not in st.session_state:
        st.session_state.confirm_rerun = False

    run_label = "▶ Run Pipeline Now" if industry_id else "▶ Run All Industries"
    if not st.session_state.confirm_rerun:
        if st.button(run_label, width="stretch", type="primary", disabled=not selected_profiles):
            if today_complete:
                st.session_state.confirm_rerun = True
                st.rerun()
            else:
                _run_pipeline_now(industry_id)
    else:
        st.warning("Today's briefing has already been created. Regenerate?")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, regenerate", width="stretch", type="primary"):
                st.session_state.confirm_rerun = False
                _run_pipeline_now(industry_id)
        with col2:
            if st.button("Cancel", width="stretch"):
                st.session_state.confirm_rerun = False
                st.rerun()

    st.divider()
    runs = get_all_runs(industry_id)
    if runs:
        st.caption(f"Total runs: {len(runs)}")
        st.caption(f"Total events: {count_events(industry_id)}")

    # ── Industry management ──
    st.divider()
    with st.expander("➕ New industry"):
        new_name = st.text_input("Industry name", placeholder="e.g. battery storage")
        if st.button("Draft profile", disabled=not new_name.strip(), width="stretch"):
            with st.spinner("Drafting profile…"):
                try:
                    from pipeline.profile_agent import draft_profile
                    st.session_state.draft_yaml_editor = draft_profile(new_name.strip()).to_yaml()
                except Exception as e:
                    st.error(f"Drafting failed: {e}")
        if st.session_state.get("draft_yaml_editor"):
            edited = st.text_area("Review and edit", key="draft_yaml_editor", height=420)
            if st.button("Save & activate", type="primary", width="stretch"):
                try:
                    profile = parse_profile(edited)
                    profile.active = True
                    save_profile(profile)
                    del st.session_state["draft_yaml_editor"]
                    st.session_state.pending_industry = profile.id
                    st.session_state.flash = ("success", f"Saved and activated {profile.label}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save profile: {e}")

    with st.expander("⚙️ Edit industries"):
        if all_profiles:
            edit_id = st.selectbox(
                "Profile",
                [p.id for p in all_profiles],
                format_func=lambda i: f"{_industry_name(i)}{'' if profiles_by_id[i].active else ' (inactive)'}",
                key="edit_profile_select",
            )
            editor_key = f"edit_yaml_{edit_id}"
            if editor_key not in st.session_state:
                st.session_state[editor_key] = profiles_by_id[edit_id].to_yaml()
            edited = st.text_area("Profile YAML", key=editor_key, height=420)
            if st.button("Save changes", width="stretch"):
                try:
                    profile = parse_profile(edited)
                    if profile.id != edit_id:
                        raise ValueError("Changing a profile's id is not supported; create a new industry instead")
                    save_profile(profile, overwrite=True)
                    del st.session_state[editor_key]
                    st.session_state.flash = ("success", f"Saved {profile.label}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save profile: {e}")


if not active_profiles:
    st.info("No active industries. Create one from the sidebar (➕ New industry) or activate a profile.")
    st.stop()


def _union(lists: list[list[str]]) -> list[str]:
    out: list[str] = []
    for items in lists:
        out.extend(i for i in items if i not in out)
    return out


segment_options = _union([p.segment_choices for p in selected_profiles])
event_type_options = _union([p.event_types for p in selected_profiles])

# ── Main content ──────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Event Tracker", "🔔 Notable Events", "📰 Briefing"])


# ── Tab 1: Company & Product Tracker ─────────────────────────────────────────
with tab1:
    st.header("Company & Product Tracker")

    col1, col2, col6, col3, col4, col5 = st.columns([2, 2, 2, 2, 2, 2])

    with col1:
        segment_filter = st.selectbox("Segment", ["All"] + segment_options)
    with col2:
        event_type_filter = st.selectbox("Event type", ["All"] + event_type_options)
    with col6:
        source_filter = st.selectbox("Source", ["All"] + list(SOURCE_LABELS), format_func=lambda s: s if s == "All" else source_label(s))
    with col3:
        date_from = st.date_input("From", value=date.today() - timedelta(days=90))
    with col4:
        date_to = st.date_input("To", value=date.today())
    with col5:
        company_filter = st.text_input("Company search", placeholder="e.g. Intuitive")

    events = get_all_events(
        industry_id=industry_id,
        segment=None if segment_filter == "All" else segment_filter,
        event_type=None if event_type_filter == "All" else event_type_filter,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        company=company_filter or None,
        source_type=None if source_filter == "All" else source_filter,
    )

    if not events:
        st.info("No events match your filters. Run the pipeline to populate data.")
    else:
        df = pd.DataFrame(events)
        df["industry_id"] = df["industry_id"].map(_industry_name)
        df["source_type"] = df["source_type"].map(source_label)
        display_cols = ["company", "product", "event_type", "segment", "date", "summary", "source_url",
                        "source_type", "funding_amount", "regulatory_status", "is_notable"]
        if not industry_id:
            display_cols.insert(0, "industry_id")
        df = df[[c for c in display_cols if c in df.columns]]

        rename_map = {
            "industry_id": "Industry",
            "company": "Company",
            "product": "Product",
            "event_type": "Event Type",
            "segment": "Segment",
            "date": "Date",
            "summary": "Summary",
            "source_url": "Source",
            "source_type": "Source Type",
            "funding_amount": "Funding ($)",
            "regulatory_status": "Regulatory Status",
            "is_notable": "Notable",
        }
        df = df.rename(columns=rename_map)

        if "Funding ($)" in df.columns:
            df["Funding ($)"] = df["Funding ($)"].apply(
                lambda x: f"${x:,.0f}" if pd.notna(x) and x else ""
            )

        st.dataframe(
            df,
            width="stretch",
            height=500,
            column_config={
                "Source": st.column_config.LinkColumn("Source"),
                "Notable": st.column_config.CheckboxColumn("Notable"),
            },
        )
        st.caption(f"{len(events)} events shown")


# ── Tab 2: Notable Events Feed ────────────────────────────────────────────────
with tab2:
    st.header("Notable Events")

    notable = get_notable_events(industry_id)

    if not notable:
        st.info("No notable events flagged in the last 7 days.")
    else:
        for e in notable:
            event_type = e.get("event_type", "news")
            icon = event_icon(event_type)
            color = event_color(event_type)
            company = html.escape(e.get("company") or "Unknown")
            product = e.get("product")
            summary = html.escape(e.get("summary", ""))
            url = e.get("source_url", "")
            ev_date = html.escape(e.get("date") or "")
            funding = e.get("funding_amount")
            regulatory = e.get("regulatory_status")
            industry_tag = "" if industry_id else f" · {html.escape(_industry_name(e.get('industry_id')))}"

            with st.container():
                product_str = f"— {html.escape(product)}" if product else ""
                funding_str = f"<strong> · ${funding:,.0f}</strong>" if funding else ""
                regulatory_str = f"<strong> · {html.escape(regulatory)}</strong>" if regulatory else ""
                card = (
                    f'<div style="border-left:4px solid {color};padding:12px 20px;margin:8px 0;background:#f8fafc;border-radius:0 8px 8px 0;color:#0f172a;">'
                    f'<span style="background:{color};color:white;padding:2px 10px;border-radius:4px;font-size:12px;font-weight:bold;">{icon} {html.escape(event_label(event_type))}</span>'
                    f'{source_badge_html(e.get("source_type"))}'
                    f'<span style="color:#64748b;font-size:13px;margin-left:12px;">{ev_date}{industry_tag}</span>'
                    f'<p style="margin:8px 0;font-size:16px;font-weight:600;">{company} {product_str}</p>'
                    f'<p style="margin:4px 0;color:#334155;">{summary}{funding_str}{regulatory_str}</p>'
                    f'</div>'
                )
                st.markdown(card, unsafe_allow_html=True)
                if url:
                    st.markdown(f"[Read source →]({url})")
                st.write("")


# ── Tab 3: Written Briefing ───────────────────────────────────────────────────
with tab3:
    for profile in selected_profiles:
        completed_runs = [
            r for r in get_all_runs(profile.id)
            if r.get("status") == "complete" and r.get("briefing_text")
        ]
        if not industry_id:
            st.header(profile.label)

        if not completed_runs:
            st.info("No briefing available yet. Run the pipeline to generate a briefing.")
            continue

        # Most recent briefing at the top
        current = completed_runs[0]
        st.subheader(f"Latest Briefing — {current['run_date']}")
        st.caption(f"Completed: {current.get('completed_at', '—')} · Triggered by: {current.get('triggered_by', '—')}")
        if current.get("source_counts"):
            counts = json.loads(current["source_counts"])
            st.caption("New results by source: " + " · ".join(
                f"{source_label(SOURCES[name].source_type if name in SOURCES else name)} {c.get('new', 0)}"
                + (" (error)" if c.get("error") else "")
                for name, c in counts.items()
            ))
        st.divider()
        st.markdown(current["briefing_text"])

        # Previous briefings
        past_runs = completed_runs[1:]
        if past_runs:
            st.divider()
            st.subheader("Previous Briefings")
            for run in past_runs:
                with st.expander(f"{run['run_date']} · {run.get('completed_at') or ''}"):
                    st.markdown(run["briefing_text"])
