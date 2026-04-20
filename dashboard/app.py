import sys
import os
import threading
import logging
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from database.db import (
    init_db,
    get_all_events,
    get_notable_events,
    get_latest_run,
    get_all_runs,
)

logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="MedRobo Radar",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# Start background scheduler once per server process
if "scheduler_started" not in st.session_state:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from config import PIPELINE_SCHEDULE_HOUR, PIPELINE_SCHEDULE_MINUTE

        def _scheduled_run():
            from pipeline.pipeline import run_pipeline
            run_pipeline(triggered_by="schedule")

        _sched = BackgroundScheduler()
        _sched.add_job(_scheduled_run, "cron", hour=PIPELINE_SCHEDULE_HOUR, minute=PIPELINE_SCHEDULE_MINUTE)
        _sched.start()
        st.session_state.scheduler_started = True
    except Exception:
        st.session_state.scheduler_started = False


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 MedRobo Radar")
    st.caption("Healthcare robotics intelligence")
    st.divider()

    latest_run = get_latest_run()
    if latest_run:
        st.metric("Last Run", latest_run["run_date"])
        status_color = {"complete": "🟢", "running": "🟡", "failed": "🔴"}.get(
            latest_run["status"], "⚪"
        )
        st.write(f"{status_color} {latest_run['status'].capitalize()}")
        if latest_run.get("triggered_by"):
            st.caption(f"Triggered by: {latest_run['triggered_by']}")
    else:
        st.info("No pipeline runs yet")

    st.divider()

    today_complete = latest_run and latest_run.get("run_date") == date.today().isoformat() and latest_run.get("status") == "complete"

    if "confirm_rerun" not in st.session_state:
        st.session_state.confirm_rerun = False

    if not st.session_state.confirm_rerun:
        if st.button("▶ Run Pipeline Now", use_container_width=True, type="primary"):
            if today_complete:
                st.session_state.confirm_rerun = True
                st.rerun()
            else:
                with st.spinner("Running pipeline…"):
                    try:
                        from pipeline.pipeline import run_pipeline
                        result = run_pipeline(triggered_by="manual", force=True)
                        if result["status"] == "complete":
                            st.success(f"Done! {result['events']} events, {result['notable']} notable")
                        else:
                            st.error(f"Pipeline failed: {result.get('error', 'unknown error')}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
    else:
        st.warning("Today's briefing has already been created. Regenerate?")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, regenerate", use_container_width=True, type="primary"):
                st.session_state.confirm_rerun = False
                with st.spinner("Running pipeline…"):
                    try:
                        from pipeline.pipeline import run_pipeline
                        result = run_pipeline(triggered_by="manual", force=True)
                        if result["status"] == "complete":
                            st.success(f"Done! {result['events']} events, {result['notable']} notable")
                        else:
                            st.error(f"Pipeline failed: {result.get('error', 'unknown error')}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
        with col2:
            if st.button("Cancel", use_container_width=True):
                st.session_state.confirm_rerun = False
                st.rerun()

    st.divider()
    runs = get_all_runs()
    if runs:
        st.caption(f"Total runs: {len(runs)}")
        all_events = get_all_events()
        st.caption(f"Total events: {len(all_events)}")


# ── Main content ──────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Event Tracker", "🔔 Notable Events", "📰 Briefing"])


# ── Tab 1: Company & Product Tracker ─────────────────────────────────────────
with tab1:
    st.header("Company & Product Tracker")

    col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 2])

    with col1:
        sub_segment_filter = st.selectbox(
            "Sub-segment",
            ["All", "surgical", "rehabilitation", "diagnostics", "exoskeletons", "ai_assisted", "other"],
        )
    with col2:
        event_type_filter = st.selectbox(
            "Event type",
            ["All", "funding", "fda", "launch", "research", "news"],
        )
    with col3:
        date_from = st.date_input("From", value=date.today() - timedelta(days=90))
    with col4:
        date_to = st.date_input("To", value=date.today())
    with col5:
        company_filter = st.text_input("Company search", placeholder="e.g. Intuitive")

    events = get_all_events(
        sub_segment=None if sub_segment_filter == "All" else sub_segment_filter,
        event_type=None if event_type_filter == "All" else event_type_filter,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        company=company_filter or None,
    )

    if not events:
        st.info("No events match your filters. Run the pipeline to populate data.")
    else:
        df = pd.DataFrame(events)
        display_cols = ["company", "product", "event_type", "sub_segment", "date", "summary", "source_url", "funding_amount", "fda_status", "is_notable"]
        df = df[[c for c in display_cols if c in df.columns]]

        rename_map = {
            "company": "Company",
            "product": "Product",
            "event_type": "Event Type",
            "sub_segment": "Sub-segment",
            "date": "Date",
            "summary": "Summary",
            "source_url": "Source",
            "funding_amount": "Funding ($)",
            "fda_status": "FDA Status",
            "is_notable": "Notable",
        }
        df = df.rename(columns=rename_map)

        if "Funding ($)" in df.columns:
            df["Funding ($)"] = df["Funding ($)"].apply(
                lambda x: f"${x:,.0f}" if pd.notna(x) and x else ""
            )

        st.dataframe(
            df,
            use_container_width=True,
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

    notable = get_notable_events()

    if not notable:
        st.info("No notable events flagged yet.")
    else:
        TYPE_COLORS = {
            "funding": "#22c55e",
            "fda": "#3b82f6",
            "launch": "#f59e0b",
            "research": "#8b5cf6",
            "news": "#6b7280",
        }
        TYPE_ICONS = {
            "funding": "💰",
            "fda": "🏛️",
            "launch": "🚀",
            "research": "🔬",
            "news": "📰",
        }

        for e in notable:
            event_type = e.get("event_type", "news")
            icon = TYPE_ICONS.get(event_type, "📌")
            color = TYPE_COLORS.get(event_type, "#6b7280")
            company = e.get("company") or "Unknown"
            product = e.get("product")
            summary = e.get("summary", "")
            url = e.get("source_url", "")
            ev_date = e.get("date", "")
            funding = e.get("funding_amount")
            fda = e.get("fda_status")

            with st.container():
                product_str = f"— {product}" if product else ""
                funding_str = f"<strong> · ${funding:,.0f}</strong>" if funding else ""
                fda_str = f"<strong> · {fda}</strong>" if fda else ""
                html = (
                    f'<div style="border-left:4px solid {color};padding:12px 20px;margin:8px 0;background:#f8fafc;border-radius:0 8px 8px 0;">'
                    f'<span style="background:{color};color:white;padding:2px 10px;border-radius:4px;font-size:12px;font-weight:bold;">{icon} {event_type.upper()}</span>'
                    f'<span style="color:#64748b;font-size:13px;margin-left:12px;">{ev_date}</span>'
                    f'<p style="margin:8px 0;font-size:16px;font-weight:600;">{company} {product_str}</p>'
                    f'<p style="margin:4px 0;color:#334155;">{summary}{funding_str}{fda_str}</p>'
                    f'</div>'
                )
                st.markdown(html, unsafe_allow_html=True)
                if url:
                    st.markdown(f"[Read source →]({url})")
                st.write("")


# ── Tab 3: Written Briefing ───────────────────────────────────────────────────
with tab3:
    all_runs = get_all_runs()
    completed_runs = [r for r in all_runs if r.get("status") == "complete" and r.get("briefing_text")]

    if not completed_runs:
        st.info("No briefing available yet. Run the pipeline to generate a briefing.")
    else:
        # Most recent briefing at the top
        current = completed_runs[0]
        st.subheader(f"Today's Briefing — {current['run_date']}")
        st.caption(f"Completed: {current.get('completed_at', '—')} · Triggered by: {current.get('triggered_by', '—')}")
        st.divider()
        st.markdown(current["briefing_text"])

        # Previous briefings
        past_runs = completed_runs[1:]
        if past_runs:
            st.divider()
            st.subheader("Previous Briefings")
            for run in past_runs:
                with st.expander(run["run_date"]):
                    st.markdown(run["briefing_text"])

