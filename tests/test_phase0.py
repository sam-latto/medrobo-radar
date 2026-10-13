from pipeline import extraction_agent, synthesis_agent
from pipeline.models import RawResult, ExtractedEvent
from profiles.loader import load_profile

PROFILE = load_profile("healthcare_robotics")


def _raw(i: int) -> RawResult:
    return RawResult(
        title=f"Title {i}",
        url=f"https://example.com/{i}",
        snippet=f"Snippet {i}",
        source="example.com",
        date="2026-09-01",
        query_tag="funding:surgical",
    )


def test_extraction_batches_and_maps_results(monkeypatch):
    calls = []

    def fake_call_tool(prompt, tool, max_tokens=4096, system=None, **kwargs):
        n = prompt.count("[id=")
        calls.append(n)
        return {
            "events": [
                {"result_id": i, "event_type": "funding", "segment": "surgical", "summary": f"s{i}"}
                for i in range(n)
            ]
        }

    monkeypatch.setattr("pipeline.llm.call_tool", fake_call_tool)
    monkeypatch.setattr(extraction_agent, "EXTRACTION_BATCH_SIZE", 15)

    raw = [_raw(i) for i in range(40)]
    events = extraction_agent.run_extraction_agent(raw, PROFILE)

    assert calls == [15, 15, 10]
    assert [e.source_url for e in events] == [r.url for r in raw]
    assert all(e.date == "2026-09-01" for e in events)


def test_extraction_retries_failed_batch_per_item(monkeypatch):
    def fake_call_tool(prompt, tool, max_tokens=4096, system=None, **kwargs):
        if prompt.count("[id=") > 1:
            raise RuntimeError("batch blew up")
        if "Title 2" in prompt:
            raise RuntimeError("bad item")
        return {"events": [{"result_id": 0, "event_type": "news", "segment": "other", "summary": "ok"}]}

    monkeypatch.setattr("pipeline.llm.call_tool", fake_call_tool)
    raw = [_raw(i) for i in range(4)]
    events = extraction_agent.run_extraction_agent(raw, PROFILE)

    assert sorted(e.source_url for e in events) == sorted(r.url for i, r in enumerate(raw) if i != 2)


def test_extraction_ignores_bad_result_ids(monkeypatch):
    monkeypatch.setattr(
        "pipeline.llm.call_tool",
        lambda *a, **k: {
            "events": [
                {"result_id": 0, "event_type": "news", "segment": "other", "summary": "a"},
                {"result_id": 0, "event_type": "news", "segment": "other", "summary": "dup"},
                {"result_id": 99, "event_type": "news", "segment": "other", "summary": "out of range"},
            ]
        },
    )
    events = extraction_agent.run_extraction_agent([_raw(0), _raw(1)], PROFILE)
    assert [e.summary for e in events] == ["a"]


def test_synthesis_uses_tool_output(monkeypatch):
    events = [
        ExtractedEvent(event_type="regulatory", segment="surgical", summary="cleared", source_url="u0"),
        ExtractedEvent(event_type="news", segment="other", summary="n", source_url="u1"),
        ExtractedEvent(event_type="launch", segment="other", summary="l", source_url="u2"),
    ]
    monkeypatch.setattr(
        "pipeline.llm.call_tool",
        lambda *a, **k: {"briefing_text": "Hello briefing"},
    )
    text, notable = synthesis_agent.run_synthesis_agent(events, PROFILE)
    assert text == "Hello briefing"
    assert notable == [0]  # rule-based only (regulatory); Claude no longer picks notable events
