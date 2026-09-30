from apscheduler.schedulers.background import BackgroundScheduler

import main
from email_digest import sender
from pipeline import synthesis_agent
from pipeline.models import ExtractedEvent
from profiles.loader import load_profile

PROFILE = load_profile("healthcare_robotics")


def test_synthesis_includes_previous_briefings(monkeypatch):
    prompts = []

    def fake_call_tool(prompt, tool, max_tokens=4096, system=None):
        prompts.append(prompt)
        return {"briefing_text": "b", "additional_notable_indices": []}

    monkeypatch.setattr(synthesis_agent, "call_tool", fake_call_tool)
    events = [ExtractedEvent(event_type="news", summary="s", source_url="u")]
    synthesis_agent.run_synthesis_agent(events, PROFILE, [
        {"run_date": "2026-09-28", "briefing_text": "Yesterday: CMR raised $200M."},
        {"run_date": "2026-09-27", "briefing_text": "x" * 10_000},
    ])
    assert "Briefing of 2026-09-28" in prompts[0] and "CMR raised $200M" in prompts[0]
    assert "following up on" in prompts[0]
    assert "x" * (synthesis_agent.PREVIOUS_BRIEFING_CHARS + 1) not in prompts[0]  # truncated

    synthesis_agent.run_synthesis_agent(events, PROFILE)
    assert "previous briefings" not in prompts[1]


def test_scheduled_failure_schedules_retry(monkeypatch):
    import pipeline.pipeline as pl
    monkeypatch.setattr(pl, "run_pipeline", lambda **kw: {
        "status": "partial", "events": 3, "notable": 1,
        "runs": [{"industry_id": "a", "status": "complete"}, {"industry_id": "b", "status": "failed"}],
    })
    scheduler = main._build_scheduler(BackgroundScheduler)
    main._run_pipeline(scheduler)
    retry_jobs = [j for j in scheduler.get_jobs() if j.func is main._retry_failed]
    assert len(retry_jobs) == 1 and retry_jobs[0].args == (["b"],)


def test_retry_alerts_only_on_repeat_failure(monkeypatch):
    import pipeline.pipeline as pl
    outcomes = {"a": {"status": "complete"}, "b": {"status": "failed", "error": "Tavily down"}}
    monkeypatch.setattr(pl, "run_pipeline", lambda profile_id, triggered_by: outcomes[profile_id])
    alerts = []
    monkeypatch.setattr(sender, "send_failure_alert", alerts.append)
    main._retry_failed(["a", "b"])
    assert alerts == [[("b", "Tavily down")]]


def test_failure_alert_email(monkeypatch):
    sent = []
    monkeypatch.setattr(sender, "EMAIL_FROM", "from@example.com")
    monkeypatch.setattr(sender, "EMAIL_TO", "to@example.com")
    monkeypatch.setattr(sender, "_send", lambda subject, body: sent.append((subject, body)))
    sender.send_failure_alert([("physical_ai", "boom <script>")])
    subject, body = sent[0]
    assert subject == "Industry Radar — pipeline failed (1 industry)"
    assert "physical_ai" in body and "&lt;script&gt;" in body
