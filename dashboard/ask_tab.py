"""💬 Ask tab: plain-English questions answered from the collected data."""
import streamlit as st

from pipeline.event_styles import event_label, source_label
from pipeline.text_utils import md_safe

EXAMPLE_QUESTIONS = [
    "Which surgical robotics companies raised over $100M this year?",
    "Summarize all FDA clearances in the last 30 days",
    "Compare funding in exoskeletons vs rehab robots by quarter",
]


def _render_sources(cited_events: list[dict], industry_name) -> None:
    if not cited_events:
        return
    with st.expander(f"Sources ({len(cited_events)})"):
        for e in cited_events:
            meta = " · ".join(
                x for x in [e.get("date"), event_label(e["event_type"]), source_label(e.get("source_type")),
                            industry_name(e.get("industry_id"))] if x
            )
            st.markdown(
                f"**[E{e['id']}]** {md_safe(e.get('company') or 'Unknown')} — {md_safe(e['summary'])}  \n"
                f"<small>{meta} · [source]({e['source_url']})</small>",
                unsafe_allow_html=True,
            )


def _render_turn(turn: dict, industry_name) -> None:
    with st.chat_message(turn["role"]):
        st.markdown(md_safe(turn["content"]))
        if turn["role"] == "assistant":
            if turn.get("hit_iteration_cap"):
                st.caption("Stopped after the maximum number of lookups; the answer may be incomplete.")
            _render_sources(turn.get("cited_events", []), industry_name)
            if turn.get("tool_calls"):
                with st.expander(f"Lookups ({len(turn['tool_calls'])})"):
                    st.json(turn["tool_calls"], expanded=False)


def render(industry_id: str | None, industry_name) -> None:
    st.header("Ask")
    scope = industry_id or "all"
    if st.session_state.get("ask_scope") != scope:
        st.session_state.ask_scope = scope
        st.session_state.ask_turns = []
        st.session_state.ask_history = []

    top = st.columns([6, 1])
    with top[0]:
        st.caption(
            f"Answers come only from the collected data for **{industry_name(industry_id) if industry_id else 'all industries'}**"
            " (change the scope in the sidebar). Every claim is cited to an event."
        )
    with top[1]:
        if st.button("Clear", width="stretch", disabled=not st.session_state.ask_turns):
            st.session_state.ask_turns = []
            st.session_state.ask_history = []
            st.rerun()

    if not st.session_state.ask_turns:
        st.write("Try one of these:")
        cols = st.columns(len(EXAMPLE_QUESTIONS))
        for col, q in zip(cols, EXAMPLE_QUESTIONS):
            if col.button(q, width="stretch"):
                st.session_state.ask_pending = q

    for turn in st.session_state.ask_turns:
        _render_turn(turn, industry_name)

    typed = st.chat_input("Ask about companies, funding, regulatory events, trends…")
    question = typed or st.session_state.pop("ask_pending", None)
    if not question:
        return

    user_turn = {"role": "user", "content": question}
    st.session_state.ask_turns.append(user_turn)
    _render_turn(user_turn, industry_name)
    with st.spinner("Looking through the data…"):
        try:
            from pipeline.query_agent import ask
            result, history = ask(question, st.session_state.ask_history, industry_id)
            st.session_state.ask_history = history
            turn = {
                "role": "assistant",
                "content": result.answer,
                "cited_events": result.cited_events,
                "tool_calls": result.tool_calls,
                "hit_iteration_cap": result.hit_iteration_cap,
            }
        except Exception as e:
            turn = {"role": "assistant", "content": f"Sorry — that failed: {e}"}
    st.session_state.ask_turns.append(turn)
    _render_turn(turn, industry_name)
