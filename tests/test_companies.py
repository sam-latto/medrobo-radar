from datetime import date

import pytest

import database.db as db
from database import companies as company_db
from pipeline import companies, momentum
from profiles.loader import load_profile


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "c.db"))
    db.init_db()


@pytest.mark.parametrize("raw,expected", [
    ("Intuitive Surgical, Inc.", "intuitive surgical"),
    ("Intuitive Surgical (NASDAQ: ISRG)", "intuitive surgical"),
    ("The Boston Dynamics Company", "boston dynamics"),
    ("ABB Ltd.", "abb"),
    ("Johnson & Johnson MedTech", "johnson and johnson medtech"),
    ("Siemens AG", "siemens"),
    ("Unknown", ""),
    (None, ""),
])
def test_normalize_name(raw, expected):
    assert companies.normalize_name(raw) == expected


def test_exact_matches_need_no_llm(fresh_db, monkeypatch):
    monkeypatch.setattr(companies, "call_tool", lambda *a, **k: pytest.fail("no LLM call expected"))
    first = companies.resolve_companies(["Medtronic plc", "Stryker"], "healthcare_robotics")
    again = companies.resolve_companies(["Medtronic", "STRYKER CORP", "Unknown"], "healthcare_robotics")
    assert again["Medtronic"] == first["Medtronic plc"]
    assert again["STRYKER CORP"] == first["Stryker"]
    assert "Unknown" not in again
    assert len(company_db.list_companies()) == 2


def test_near_matches_are_confirmed_by_claude(fresh_db, monkeypatch):
    ids = companies.resolve_companies(["Intuitive Surgical", "Figure AI"], "healthcare_robotics")
    prompts = []

    def fake_call_tool(prompt, tool, max_tokens=4096, system=None):
        prompts.append(prompt)
        # [0] "Intuitive" is the same company; [1] "Figure AI Robotics Lab" is not
        return {"matches": [{"index": 0, "company_id": ids["Intuitive Surgical"]},
                            {"index": 1, "company_id": None}]}

    monkeypatch.setattr(companies, "call_tool", fake_call_tool)
    out = companies.resolve_companies(["Intuitive", "Figure AI Robotics Lab"], "healthcare_robotics")
    assert len(prompts) == 1  # one batched call
    assert out["Intuitive"] == ids["Intuitive Surgical"]
    assert out["Figure AI Robotics Lab"] not in ids.values()


def test_claude_cannot_pick_a_non_candidate(fresh_db, monkeypatch):
    companies.resolve_companies(["Intuitive Surgical"], "healthcare_robotics")
    monkeypatch.setattr(companies, "call_tool",
                        lambda *a, **k: {"matches": [{"index": 0, "company_id": 424242}]})
    out = companies.resolve_companies(["Intuitive"], "healthcare_robotics")
    assert out["Intuitive"] != 424242
    assert company_db.get_company(out["Intuitive"])["name"] == "Intuitive"


def _event(i, company, event_type, d, amount=None, product=None, industry="healthcare_robotics"):
    return {"industry_id": industry, "company": company, "event_type": event_type, "segment": "surgical",
            "date": d, "summary": f"{company} {event_type}", "source_url": f"https://ex.com/{i}",
            "funding_amount": amount, "product": product}


def test_link_events_and_momentum(fresh_db, monkeypatch):
    monkeypatch.setattr(companies, "call_tool", lambda *a, **k: {"matches": []})
    profile = load_profile("healthcare_robotics")
    events = [
        _event(1, "CMR Surgical", "funding", "2026-09-01", 200_000_000),
        _event(2, "CMR Surgical Ltd", "funding", "2026-09-02", 200_000_000),  # same round, 2nd article
        _event(3, "Moon Surgical", "regulatory", "2026-08-01", product="Maestro"),
        _event(4, "Moon Surgical", "news", "2026-08-02"),
        _event(5, "Tiny Robotics", "news", "2026-09-05"),
        _event(6, "Old Robotics", "funding", "2025-01-01", 500_000_000),  # outside the 90-day window
    ]
    for e in events:
        db.insert_event("run", e)
    linked = companies.link_events(company_db.get_unlinked_events())
    assert linked == 6
    companies.ensure_companies(profile.watchlist, profile.id)

    ranking = momentum.rank_companies(profile, as_of=date(2026, 9, 29))
    by_name = {r["company"]: r for r in ranking}
    cmr = by_name["CMR Surgical"]
    assert cmr["events_90d"] == 2
    assert cmr["funding_90d"] == 200_000_000                  # de-duplicated
    assert cmr["score"] == pytest.approx(3.0 * (1 + 2.3010), abs=0.01)  # counted once, log-scaled
    assert by_name["Moon Surgical"]["score"] == 3.0 + 0.5
    assert by_name["Moon Surgical"]["watchlist"] is True
    assert by_name["Old Robotics"]["score"] == 0
    assert by_name["Intuitive Surgical"]["watchlist"] and by_name["Intuitive Surgical"]["score"] == 0
    assert ranking[0]["company"] == "CMR Surgical"
    assert company_db.get_company(cmr["company_id"])["first_seen"] == "2026-09-01"


def test_profile_momentum_weights_override(fresh_db):
    profile = load_profile("healthcare_robotics").model_copy(update={"momentum_weights": {"news": 5.0}})
    assert momentum.weights_for(profile)["news"] == 5.0
    assert momentum.weights_for(profile)["regulatory"] == momentum.DEFAULT_WEIGHTS["regulatory"]


def test_funding_points_scale():
    assert momentum._funding_points(None) == 1.0
    assert momentum._funding_points(10_000_000) == pytest.approx(2.0)
    assert momentum._funding_points(1_000_000_000) == pytest.approx(4.0)
