"""🏢 Companies tab: momentum ranking per industry and per-company event history."""
import pandas as pd
import streamlit as st

from database import companies as company_db
from pipeline.companies import ensure_companies
from pipeline.event_styles import event_icon, event_label, source_label
from pipeline.momentum import WINDOW_DAYS, rank_companies
from pipeline.text_utils import md_safe


def _money(x) -> str:
    return f"${x:,.0f}" if x else ""


def _total_disclosed_funding(events: list[dict]) -> int:
    seen: set[int] = set()
    for e in events:
        if e["event_type"] == "funding" and e.get("funding_amount"):
            seen.add(e["funding_amount"])  # the same round is often reported more than once
    return sum(seen)


def _render_company(company: dict, industry_id: str) -> None:
    events = company_db.get_company_events(company["id"], industry_id=industry_id)
    st.subheader(company["name"])
    cols = st.columns(4)
    cols[0].metric("Events", len(events))
    cols[1].metric("Disclosed funding", _money(_total_disclosed_funding(events)) or "—")
    cols[2].metric("First seen", company.get("first_seen") or "—")
    cols[3].metric("Last seen", company.get("last_seen") or "—")

    if not events:
        st.info("No events linked to this company yet.")
        return

    regulatory = [e for e in events if e["event_type"] == "regulatory" or e.get("regulatory_status")]
    if regulatory:
        st.markdown("**Regulatory history**")
        st.dataframe(
            pd.DataFrame([{
                "Date": e.get("date"),
                "Status": e.get("regulatory_status") or "",
                "Product": e.get("product") or "",
                "Summary": e["summary"],
                "Source": e["source_url"],
                "Source Type": source_label(e.get("source_type")),
            } for e in regulatory]),
            width="stretch",
            hide_index=True,
            column_config={"Source": st.column_config.LinkColumn("Source")},
        )

    st.markdown("**Timeline**")
    for e in events:
        amount = f" · **{_money(e.get('funding_amount'))}**" if e.get("funding_amount") else ""
        st.markdown(
            md_safe(f"{event_icon(e['event_type'])} **{e.get('date') or 'undated'}** · "
                    f"{event_label(e['event_type'])}{amount} — {e['summary']}") + "  \n"
            f"<small>{source_label(e.get('source_type'))} · [source]({e['source_url']})</small>",
            unsafe_allow_html=True,
        )


def render(selected_profiles: list, industry_id: str | None) -> None:
    st.header("Companies")
    if industry_id:
        profile = selected_profiles[0]
    else:
        profile = st.selectbox(
            "Industry", selected_profiles, format_func=lambda p: p.label, key="companies_industry"
        )

    ensure_companies(profile.watchlist, profile.id)
    ranking = rank_companies(profile)

    c1, c2 = st.columns([3, 2])
    with c1:
        show_all = st.toggle("Include companies with no recent activity", value=False)
    with c2:
        watchlist_first = st.toggle("Pin watchlist to top", value=False)

    rows = [r for r in ranking if show_all or r["score"] > 0 or r["watchlist"]]
    if watchlist_first:
        rows = sorted(rows, key=lambda r: (not r["watchlist"], r["rank"]))

    st.caption(
        f"Momentum over the last {WINDOW_DAYS} days: funding (log-scaled by amount), regulatory milestones, "
        f"launches, partnerships/M&A, research, and news volume (lowest weight). Weights are set per industry. "
        f"★ = watchlist."
    )
    if not rows:
        st.info("No companies yet. Run the pipeline to populate data.")
        return

    df = pd.DataFrame([{
        "Rank": r["rank"],
        "★": "★" if r["watchlist"] else "",
        "Company": r["company"],
        "Momentum": r["score"],
        f"Events ({WINDOW_DAYS}d)": r["events_90d"],
        f"Disclosed funding ({WINDOW_DAYS}d)": _money(r["funding_90d"]),
        "Regulatory": r["regulatory_90d"],
        "Last event": r["last_event"] or "",
    } for r in rows])
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        height=min(38 * (len(df) + 1) + 3, 460),
        column_config={"Momentum": st.column_config.ProgressColumn(
            "Momentum", min_value=0, max_value=max(r["score"] for r in rows) or 1, format="%.1f")},
    )
    st.download_button(
        "⬇ Download CSV", df.to_csv(index=False).encode("utf-8"),
        file_name=f"{profile.id}_company_momentum.csv", mime="text/csv",
    )

    st.divider()
    labels = {r["company_id"]: f"{'★ ' if r['watchlist'] else ''}{r['company']}" for r in rows}
    selected_id = st.selectbox(
        "Company details",
        list(labels),
        format_func=labels.get,
        key=f"company_detail_{profile.id}",
    )
    company = company_db.get_company(selected_id)
    if company:
        _render_company(company, profile.id)
