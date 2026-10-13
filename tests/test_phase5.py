from apscheduler.schedulers.background import BackgroundScheduler

import main
from email_digest import sender
from pipeline import synthesis_agent
from pipeline.models import ExtractedEvent
from profiles.loader import load_profile

PROFILE = load_profile("healthcare_robotics")


def test_synthesis_includes_previous_briefings(monkeypatch):
    prompts = []

    def fake_call_tool(prompt, tool, max_tokens=4096, system=None, **kwargs):
        prompts.append(prompt)
        return {"briefing_text": "b", "additional_notable_indices": []}

    monkeypatch.setattr("pipeline.llm.call_tool", fake_call_tool)
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


def test_synthesis_includes_context_events(monkeypatch):
    prompts = []
    monkeypatch.setattr("pipeline.llm.call_tool", lambda prompt, tool, **k: (
        prompts.append(prompt) or {"briefing_text": "b", "additional_notable_indices": []}))
    events = [ExtractedEvent(event_type="news", summary="s", source_url="u")]
    synthesis_agent.run_synthesis_agent(events, PROFILE, context_events=[
        {"date": "2026-08-19", "company": "Vitestro", "event_type": "regulatory",
         "summary": "Aletta De Novo", "regulatory_status": "De Novo", "source_type": "openfda"},
        {"date": "2026-09-01", "company": "Medtronic", "event_type": "news",
         "summary": "Cornerstone deal", "funding_amount": 700_000_000, "source_type": "tavily"},
    ])
    assert "2026-08-19 · Vitestro · regulatory: Aletta De Novo (De Novo; openfda)" in prompts[0]
    assert "($700,000,000; tavily)" in prompts[0]
    assert "## The bottom line" in prompts[0]

    synthesis_agent.run_synthesis_agent(events, PROFILE)
    assert "Background: events tracked" not in prompts[1]


def test_markdown_to_email_html():
    from pipeline.text_utils import markdown_to_email_html
    out = markdown_to_email_html(
        "# Big news <script>\n*A dek*\n\n## The bottom line\n- **One.** First\n- Two\n\n"
        "Cleared ([openFDA](https://fda.gov/x)) and [bad](javascript:alert(1)).\n\n1. **Acme** — watch"
    )
    assert '<h2 style="font-size:24px' in out and "&lt;script&gt;" in out and "<script>" not in out
    assert "<em>A dek</em>" in out
    assert "<ul" in out and "<li style=\"margin:0 0 6px;\"><strong>One.</strong> First</li>" in out
    assert '<a href="https://fda.gov/x"' in out and 'href="javascript' not in out
    assert "<ol" in out and "<strong>Acme</strong>" in out


def test_synthesis_removes_links_not_in_the_events(monkeypatch):
    brief = ("# Head\n\nCleared ([openFDA](https://fda.gov/k1)). Also ([Made up](https://invented.example/z)). "
             "Earlier deal ([Reuters](https://reuters.com/old)).")
    monkeypatch.setattr("pipeline.llm.call_tool", lambda *a, **k: {"briefing_text": brief})
    events = [ExtractedEvent(event_type="regulatory", summary="s", source_url="https://fda.gov/k1")]
    text, _ = synthesis_agent.run_synthesis_agent(
        events, PROFILE, context_events=[{"date": "2026-09-01", "summary": "old", "source_url": "https://reuters.com/old"}])
    assert "https://fda.gov/k1" in text and "https://reuters.com/old" in text
    assert "invented.example" not in text and "Made up" not in text


def test_synthesis_prompt_says_weekly_when_the_last_brief_is_old(monkeypatch):
    from datetime import date, timedelta
    prompts = []
    monkeypatch.setattr("pipeline.llm.call_tool",
                        lambda prompt, tool, **k: prompts.append(prompt) or {"briefing_text": "b"})
    events = [ExtractedEvent(event_type="news", summary="s", source_url="u")]
    synthesis_agent.run_synthesis_agent(events, PROFILE, previous_brief_date=date.today() - timedelta(days=7))
    synthesis_agent.run_synthesis_agent(events, PROFILE, previous_brief_date=date.today() - timedelta(days=1))
    synthesis_agent.run_synthesis_agent(events, PROFILE)
    assert "the 7 days since the previous brief" in prompts[0] and "weekly brief" in prompts[0]
    assert "roughly the last day" in prompts[1] and "This is a daily brief." in prompts[1]
    assert "the first for this industry" in prompts[2]
    assert all("additional_notable_indices" not in p and "notable" not in p.lower() for p in prompts)
