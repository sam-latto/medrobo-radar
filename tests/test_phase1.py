import sqlite3

import pytest
import yaml

import database.db as db
from email_digest import sender
from pipeline import profile_agent, synthesis_agent
from pipeline.models import ExtractedEvent
from pipeline.search_agent import build_queries
from profiles.loader import IndustryProfile, load_profile, load_profiles, parse_profile


def test_bundled_profiles_load():
    ids = {p.id for p in load_profiles(active_only=True)}
    assert {"healthcare_robotics", "physical_ai", "industrial_automation"} <= ids


def test_profile_normalizes_and_adds_fallbacks():
    p = IndustryProfile(
        id="x", name="X", segments=["AI-assisted", "Surgical"], event_types=["Funding"],
        search_queries={"funding": "{segment} funding"},
        notable_rules={"always_notable_event_types": ["funding", "not_a_type"]},
        sources={"tavily": None},
    )
    assert p.segments == ["ai_assisted", "surgical"]
    assert p.event_types == ["funding", "news"]
    assert p.segment_choices[-1] == "other"
    assert p.notable_rules.always_notable_event_types == ["funding"]
    assert p.sources == {"tavily": {}}


def test_profile_rejects_queries_for_unknown_event_types():
    with pytest.raises(ValueError):
        IndustryProfile(id="x", name="X", segments=["a"], event_types=["funding"],
                        search_queries={"regulatory": "q"})


def test_profile_yaml_round_trip():
    p = load_profile("healthcare_robotics")
    assert parse_profile(p.to_yaml()) == p


def test_event_coerced_to_profile_vocabulary():
    p = load_profile("healthcare_robotics")
    e = ExtractedEvent.model_validate(
        {"event_type": "FDA", "segment": "Exoskeletons", "summary": "s", "source_url": "u"},
        context={"profile": p},
    )
    assert e.event_type == "news"  # 'fda' isn't in this profile's vocabulary
    assert e.segment == "exoskeletons"
    e = ExtractedEvent.model_validate(
        {"event_type": "partnership_ma", "segment": "space", "summary": "s", "source_url": "u"},
        context={"profile": p},
    )
    assert (e.event_type, e.segment) == ("partnership_ma", "other")


def test_build_queries_crosses_segments_and_has_no_years():
    p = load_profile("healthcare_robotics")
    queries = build_queries(p)
    assert len(queries) == len(p.search_queries) * len(p.segments)
    assert ("ai assisted robotics company funding round investment", "funding:ai_assisted") in queries
    assert not any(ch.isdigit() for q, _ in queries for ch in q)


def test_rule_notable_uses_profile_rules():
    p = load_profile("physical_ai")  # threshold $100M, partnership_ma always notable

    def mk(**kw):
        return ExtractedEvent(summary="s", source_url="u", segment="humanoids", **kw)

    assert synthesis_agent.is_rule_notable(mk(event_type="partnership_ma"), p)
    assert synthesis_agent.is_rule_notable(mk(event_type="funding", funding_amount=150_000_000), p)
    assert not synthesis_agent.is_rule_notable(mk(event_type="funding", funding_amount=60_000_000), p)


def _make_v1_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE pipeline_runs (id TEXT PRIMARY KEY, run_date DATE NOT NULL, triggered_by TEXT NOT NULL,
            status TEXT NOT NULL, briefing_text TEXT, completed_at TIMESTAMP);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, company TEXT,
            product TEXT, event_type TEXT NOT NULL, sub_segment TEXT NOT NULL, date DATE, summary TEXT NOT NULL,
            source_url TEXT NOT NULL, funding_amount INTEGER, fda_status TEXT,
            is_notable BOOLEAN NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE UNIQUE INDEX idx_events_source_url ON events(source_url);
        INSERT INTO pipeline_runs VALUES ('r1', '2026-04-19', 'manual', 'complete', 'hello', '2026-04-19 10:00:00');
        INSERT INTO events (run_id, company, event_type, sub_segment, summary, source_url, fda_status, is_notable)
            VALUES ('r1', 'Acme', 'fda', 'surgical', 'cleared', 'https://a', '510(k) Cleared', 1),
                   ('r1', 'Beta', 'funding', 'diagnostics', 'raised', 'https://b', NULL, 0);
    """)
    conn.commit()
    conn.close()


def test_v1_migration(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    _make_v1_db(path)
    monkeypatch.setattr(db, "DB_PATH", str(path))

    db.init_db()
    db.init_db()  # idempotent

    events = {e["company"]: e for e in db.get_all_events()}
    assert events["Acme"]["industry_id"] == "healthcare_robotics"
    assert events["Acme"]["event_type"] == "regulatory"
    assert events["Acme"]["segment"] == "surgical"
    assert events["Acme"]["regulatory_status"] == "510(k) Cleared"
    assert events["Acme"]["source_type"] == "tavily"
    assert db.get_latest_run("healthcare_robotics")["briefing_text"] == "hello"
    assert list(tmp_path.glob("v1.db.bak-v1-*")), "backup should be written before migrating"

    # Same URL may now exist once per industry
    base = {"event_type": "news", "segment": "other", "summary": "s", "source_url": "https://a"}
    assert db.insert_event("r2", {**base, "industry_id": "physical_ai"}) is not None
    assert db.insert_event("r2", {**base, "industry_id": "physical_ai"}) is None
    assert db.url_exists("https://a", "physical_ai")
    assert not db.url_exists("https://a", "industrial_automation")


def test_draft_profile(monkeypatch):
    fake = {
        "name": "Battery Storage",
        "description": "Stationary energy storage.",
        "segments": ["grid scale storage", "residential storage"],
        "event_types": ["funding", "deployment", "news"],
        "search_queries": {
            "funding": "{segment} battery startup raises 2025",
            "deployment": "{segment} battery project commissioned",
            "bogus": "ignored",
        },
        "notable_rules": {"funding_threshold_usd": 75000000, "always_notable_event_types": ["deployment"]},
        "sources": {"tavily": {}},
        "watchlist": ["Fluence", "Form Energy", "Tesla"],
        "briefing_audience": "climate-tech investor",
    }
    monkeypatch.setattr(profile_agent, "call_tool", lambda *a, **k: dict(fake))
    p = profile_agent.draft_profile("battery storage")
    assert p.id == "battery_storage"
    assert p.active is False
    assert p.segments == ["grid_scale_storage", "residential_storage"]
    assert p.search_queries["funding"] == "{segment} battery startup raises"
    assert "bogus" not in p.search_queries
    assert yaml.safe_load(p.to_yaml())["id"] == "battery_storage"


def test_digest_modes():
    sections = [
        {"industry_name": "A", "briefing_text": "Brief A <b>", "notable_events": [
            {"event_type": "funding", "company": "Co", "summary": "raised", "source_url": "https://x"}]},
        {"industry_name": "B", "briefing_text": "Brief B", "notable_events": []},
    ]
    combined = sender.build_digests(sections, "2026-09-29", mode="combined")
    assert len(combined) == 1
    assert "Brief A &lt;b&gt;" in combined[0][1] and "Brief B" in combined[0][1]
    per = sender.build_digests(sections, "2026-09-29", mode="per_industry")
    assert [s for s, _ in per] == ["A — 2026-09-29", "B — 2026-09-29"]


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-15", "2026-09-15"),
    ("2026-09-15T10:00:00Z", "2026-09-15"),
    ("2026-09", "2026-09-01"),
    ("Mon, 15 Sep 2026 10:00:00 GMT", "2026-09-15"),
    ("<UNKNOWN>", None),
    ("", None),
    (None, None),
])
def test_event_date_normalized(raw, expected):
    e = ExtractedEvent(event_type="news", summary="s", source_url="u", date=raw)
    assert e.date == expected
