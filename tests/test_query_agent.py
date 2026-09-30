from types import SimpleNamespace

import pytest

import database.db as db
from database import queries
from pipeline import query_agent


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "q.db"))
    db.init_db()
    rows = [
        # industry, company, event_type, segment, date, funding, source_type
        ("healthcare_robotics", "CMR Surgical", "funding", "surgical", "2026-03-10", 200_000_000, "tavily"),
        ("healthcare_robotics", "CMR Surgical", "funding", "surgical", "2026-03-11", 200_000_000, "tavily"),
        ("healthcare_robotics", "Moon Surgical", "funding", "surgical", "2026-07-01", 55_000_000, "sec_form_d"),
        ("healthcare_robotics", "Ekso Bionics", "funding", "exoskeletons", "2026-05-02", 30_000_000, "tavily"),
        ("healthcare_robotics", "Hocoma", "funding", "rehabilitation", "2026-08-15", 12_000_000, "tavily"),
        ("healthcare_robotics", "THINK Surgical", "regulatory", "surgical", "2026-09-10", None, "openfda"),
        ("healthcare_robotics", "Old Co", "funding", "surgical", "2025-06-01", 150_000_000, "tavily"),
        ("physical_ai", "Figure AI", "funding", "humanoids", "2026-09-01", 1_000_000_000, "tavily"),
    ]
    for i, (ind, co, et, seg, d, amt, st) in enumerate(rows):
        db.insert_event("run", {
            "industry_id": ind, "company": co, "event_type": et, "segment": seg, "date": d,
            "summary": f"{co} {et}", "source_url": f"https://ex.com/{i}", "funding_amount": amt,
            "source_type": st, "regulatory_status": "FDA 510(k) Cleared" if et == "regulatory" else None,
        })
    db.insert_run("r1", "2026-09-29", "manual", "healthcare_robotics")
    db.update_run("r1", "complete", "Healthcare briefing text")
    return rows


def test_search_events_filters(seeded_db):
    events = queries.search_events(industry_id="healthcare_robotics", segment="surgical",
                                   event_type="funding", min_funding=100_000_000, date_from="2026-01-01")
    assert {e["company"] for e in events} == {"CMR Surgical"}
    assert queries.count_matching_events(industry_id="physical_ai") == 1
    assert queries.search_events(text="THINK")[0]["source_type"] == "openfda"


def test_search_is_parameterized(seeded_db):
    # Injection attempts are just strings that match nothing
    assert queries.search_events(company="x' OR '1'='1") == []
    assert queries.search_events(text="%'; DROP TABLE events; --") == []
    assert queries.count_matching_events() == len(seeded_db)


def test_aggregate_by_quarter_and_segment(seeded_db):
    rows = queries.aggregate_events("quarter", industry_id="healthcare_robotics", event_type="funding",
                                    date_from="2026-01-01")
    assert [r["quarter"] for r in rows] == ["2026-Q1", "2026-Q2", "2026-Q3"]
    q2 = rows[1]
    assert q2["event_count"] == 1 and q2["total_funding_usd"] == 30_000_000
    by_seg = {r["segment"]: r for r in queries.aggregate_events("segment", industry_id="healthcare_robotics")}
    assert by_seg["surgical"]["event_count"] == 5
    with pytest.raises(ValueError):
        queries.aggregate_events("id; DROP TABLE events")


def test_run_tool_defaults_to_viewed_industry(seeded_db):
    out = query_agent.run_tool("search_events", {"event_type": "funding"}, "physical_ai")
    assert out["total_matching"] == 1
    out = query_agent.run_tool("search_events", {"event_type": "funding", "industry_id": "all"}, "physical_ai")
    assert out["total_matching"] == 7
    out = query_agent.run_tool("get_briefings", {}, "healthcare_robotics")
    assert out["briefings"][0]["briefing_text"] == "Healthcare briefing text"


def _block(**kw):
    return SimpleNamespace(**kw)


def test_ask_loop_runs_tools_and_links_citations(seeded_db, monkeypatch):
    think_id = queries.search_events(text="THINK")[0]["id"]
    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[
            _block(type="tool_use", id="t1", name="search_events",
                   input={"event_type": "regulatory", "date_from": "2026-08-30"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[
            _block(type="text", text=f"THINK Surgical got a 510(k) [E{think_id}] and [E99999]."),
        ]),
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(query_agent.client.messages, "create", fake_create)
    result, history = query_agent.ask("FDA clearances last 30 days?", [], "healthcare_robotics")

    assert calls[0]["tool_choice"] == {"type": "auto"}
    tool_result = history[2]["content"][0]
    assert tool_result["type"] == "tool_result" and '"total_matching": 1' in tool_result["content"]
    assert f"[[E{think_id}]](https://ex.com/5)" in result.answer
    assert "[E99999]" in result.answer  # unknown ids are left as-is
    assert [e["id"] for e in result.cited_events] == [think_id]
    assert result.tool_calls[0]["tool"] == "search_events"
    assert len(history) == 4  # user, assistant(tool_use), user(tool_result), assistant(answer)


def test_ask_caps_iterations(seeded_db, monkeypatch):
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if kwargs["tool_choice"] == {"type": "none"}:
            return SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text="Partial answer")])
        return SimpleNamespace(stop_reason="tool_use", content=[
            _block(type="tool_use", id=f"t{len(calls)}", name="search_events", input={}),
        ])

    monkeypatch.setattr(query_agent.client.messages, "create", fake_create)
    result, _ = query_agent.ask("loop forever", [], None)
    assert len(calls) == query_agent.MAX_TOOL_ITERATIONS + 1
    assert result.hit_iteration_cap
    assert result.answer == "Partial answer"


def test_tool_errors_are_returned_to_model(seeded_db, monkeypatch):
    responses = iter([
        SimpleNamespace(stop_reason="tool_use", content=[
            _block(type="tool_use", id="t1", name="aggregate_events", input={"group_by": "bogus"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text="ok")]),
    ])
    monkeypatch.setattr(query_agent.client.messages, "create", lambda **k: next(responses))
    result, history = query_agent.ask("q", [], None)
    assert history[2]["content"][0]["is_error"] is True
    assert "error" in result.tool_calls[0]


def test_search_reports_undated_matches(seeded_db):
    db.insert_event("run", {"industry_id": "healthcare_robotics", "company": "Undated Co", "event_type": "funding",
                            "segment": "surgical", "date": None, "summary": "raised", "source_url": "https://ex.com/u",
                            "funding_amount": 300_000_000})
    out = query_agent.run_tool("search_events", {"event_type": "funding", "date_from": "2026-01-01",
                                                 "min_funding": 100_000_000}, "healthcare_robotics")
    assert out["total_matching"] == 2  # the two dated CMR rows
    assert out["undated_matching_other_filters"] == 1
    out = query_agent.run_tool("search_events", {"event_type": "funding", "undated_only": True}, "healthcare_robotics")
    assert [e["company"] for e in out["events"]] == ["Undated Co"]
    assert "undated_matching_other_filters" not in query_agent.run_tool("search_events", {}, "healthcare_robotics")
