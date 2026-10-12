"""Offline tests for the SQLite -> Postgres copy helpers."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "migrate_sqlite_to_postgres",
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def test_headline_from_research_brief():
    md = "# Medtronic's $700M bet reshapes the race\n\nDeck line\n\n## The bottom line\n- a"
    assert mig.brief_headline(md) == "Medtronic's $700M bet reshapes the race"


def test_headline_skips_old_title_and_uses_lead_story():
    md = ("## MedRobo Radar — Daily Briefing | September 29, 2026\n\n"
          "**Lead Story: Medtronic Doubles Down on Surgical Robotics**\nBody text")
    assert mig.brief_headline(md) == "Medtronic Doubles Down on Surgical Robotics"


def test_headline_none_when_no_heading_or_bold():
    assert mig.brief_headline("just a paragraph\nand another") is None


def test_pick_briefs_takes_latest_complete_real_brief_per_day():
    runs = [
        {"id": "a", "industry_id": "x", "run_date": "2026-09-29", "status": "complete",
         "briefing_text": "# Early", "completed_at": "2026-09-29 08:00:00"},
        {"id": "b", "industry_id": "x", "run_date": "2026-09-29", "status": "complete",
         "briefing_text": "# Late", "completed_at": "2026-09-29 09:00:00"},
        {"id": "c", "industry_id": "x", "run_date": "2026-09-29", "status": "failed",
         "briefing_text": "# Failed", "completed_at": "2026-09-29 10:00:00"},
        {"id": "d", "industry_id": "x", "run_date": "2026-09-30", "status": "complete",
         "briefing_text": "No new events today.", "completed_at": "2026-09-30 08:00:00"},
        {"id": "e", "industry_id": "y", "run_date": "2026-09-29", "status": "complete",
         "briefing_text": "# Other industry", "completed_at": None},
    ]
    assert sorted(b["id"] for b in mig.pick_briefs(runs)) == ["b", "e"]


def test_job_preferences_row_maps_old_settings():
    prefs = {"functions": ["Product Management", "Strategy"], "custom_functions": "BizOps",
             "locations": "boston, New York ,", "include_remote": True, "remote_only": False}
    row = mig.job_preferences_row(prefs, ["physical_ai"])
    assert row == {
        "role_types": ["Product Management", "Strategy"],
        "custom_role_types": "BizOps",
        "locations": ["boston", "New York"],
        "include_remote": True,
        "remote_only": False,
        "industry_ids": ["physical_ai"],
    }


def test_job_preferences_row_defaults():
    row = mig.job_preferences_row({}, [])
    assert row["role_types"] == [] and row["locations"] == []
    assert row["include_remote"] is True and row["remote_only"] is False


def test_small_converters():
    assert mig.json_list('["a", "b"]') == ["a", "b"]
    assert mig.json_list("not json") == []
    assert mig.json_list('{"a": 1}') == []
    assert mig.json_or_none(None) is None
    assert mig.as_bool(None) is None and mig.as_bool(0) is False and mig.as_bool(1) is True
    assert mig.utc("2026-09-30 01:50:48") == "2026-09-30 01:50:48+00"
    assert mig.utc(None) is None
