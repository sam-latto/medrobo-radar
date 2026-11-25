"""Company "what they do" descriptions (pipeline/company_descriptions.py)."""
import os

import pytest

from pipeline import company_descriptions as cd

GROUP = [{"id": 1, "name": "Mecka AI", "summaries": ["Mecka AI raised $60M to collect human motion data for robots."]},
         {"id": 2, "name": "University of Michigan", "summaries": ["A lab published a gait study."]}]


def test_request_is_grounded_in_the_events_and_uses_the_cheap_model():
    req = cd.build_request(GROUP, "Physical AI")
    assert req["model"] == cd.EXTRACTION_MODEL and req["tool"]["name"] == "record_descriptions"
    p = req["prompt"]
    assert "[id=1] Mecka AI" in p and "- Mecka AI raised $60M" in p
    assert "Use only what the summaries say" in p and "return an empty description" in p
    assert "no amounts, dates or deals" in p


def test_parse_keeps_valid_entries_only():
    data = {"companies": [
        {"id": 1, "description": "  Collects   human motion data to train robots. "},
        {"id": 2, "description": ""},                      # events don't say: left empty
        {"id": 99, "description": "Not in this group"},    # unknown id ignored
        {"id": 1, "description": "x" * 400},               # later duplicate for id 1 wins, but is capped
    ]}
    out = cd.parse_descriptions(data, GROUP)
    assert list(out) == [1] and len(out[1]) <= cd.MAX_CHARS + 1 and out[1].endswith("…")
    assert cd.parse_descriptions({"companies": [{"id": 1, "description": "Makes robots."}]}, GROUP) == {1: "Makes robots."}
    assert cd.parse_descriptions(None, GROUP) == {}


@pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="needs the dev database (RUN_DB_TESTS=1)")
def test_describe_companies_fills_only_missing_descriptions(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from config import DATABASE_URL

    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn.execute("select 1")  # open the outer transaction so everything is rolled back
    try:
        new = conn.execute("insert into public.companies (name, normalized_name) values "
                           "('Describe Me Robotics', 'describe me robotics'), ('Already Described', 'already described') "
                           "returning id").fetchall()
        a, b = new[0]["id"], new[1]["id"]
        conn.execute("update public.companies set description = 'Kept as is.' where id = %s", (b,))
        run = conn.execute("select id from public.pipeline_runs where industry_id = 'physical_ai' limit 1").fetchone()
        for cid, url in ((a, "https://www.therobotreport.com/describe-a"), (b, "https://www.therobotreport.com/describe-b")):
            conn.execute("insert into public.company_industries (company_id, industry_id) values (%s, 'physical_ai')", (cid,))
            conn.execute("insert into public.events (run_id, industry_id, company_id, company, event_type, segment, "
                         "summary, source_url, source_type) values (%s, 'physical_ai', %s, 'x', 'launch', 'other', "
                         "'Launched a warehouse picking robot.', %s, 'tavily')", (run["id"], cid, url))
        asked = []

        def fake_call_tools(requests):
            asked.extend(requests)
            return [{"companies": [{"id": a, "description": "Builds warehouse picking robots."},
                                   {"id": b, "description": "Should not overwrite."}]} for _ in requests]

        monkeypatch.setattr(cd, "call_tools", fake_call_tools)
        missing_ids = {c["id"] for c in cd.missing_descriptions(conn, "physical_ai", limit=1000)}
        assert a in missing_ids and b not in missing_ids
        cd.describe_companies(conn, "physical_ai", "Physical AI", limit=1000)
        got = {r["id"]: r["description"] for r in conn.execute(
            "select id, description from public.companies where id = any(%s)", ([a, b],))}
        assert got == {a: "Builds warehouse picking robots.", b: "Kept as is."}
        assert all(f"[id={b}]" not in r["prompt"] for r in asked)  # never asked about described companies
    finally:
        conn.rollback()
        conn.close()
