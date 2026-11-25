"""Duplicate merge (pipeline/dedupe.py) and the brief's key-items list, on cases from the
Oct 12 physical_ai run."""
import logging

from pipeline import synthesis_agent
from pipeline.dedupe import merge_duplicates, same_story
from pipeline.extraction_agent import _extract_request
from pipeline.models import ExtractedEvent
from profiles.loader import load_profile

PROFILE = load_profile("physical_ai")


def ev(company, event_type="funding", amount=None, date=None, source_type="tavily", url=None, product=None,
       summary="s"):
    return ExtractedEvent(company=company, event_type=event_type, funding_amount=amount, date=date,
                          source_type=source_type, source_url=url or f"https://x.example/{company}/{summary}",
                          product=product, summary=summary, segment="other")


def test_same_round_from_several_outlets_becomes_one_event():
    events = [
        ev("Humanoid", amount=152_000_000, date="2026-10-08", summary="Humanoid raised $152M Series A"),
        ev("Humanoid Ltd.", amount=152_000_000, summary="UK-based Humanoid raised a $152M Series A at $1.35B"),
        ev("Mecka AI", amount=60_000_000, date="2026-10-07", summary="Mecka AI raised $60M"),
        ev("Mecka AI", amount=60_000_000, date="2026-10-07", summary="BetaKit: Mecka AI's $60M Series B"),
    ]
    merged = merge_duplicates(events)
    assert [(e.company, e.funding_amount) for e in merged] == [("Humanoid", 152_000_000), ("Mecka AI", 60_000_000)]
    # Missing fields are filled from the copies
    assert merged[0].date == "2026-10-08"


def test_amount_match_merges_across_event_types():
    # Helm.ai's $70M in contracts was recorded once as news and once as funding
    merged = merge_duplicates([ev("Helm.ai", "news", 70_000_000), ev("Helm.ai", "funding", 70_000_000),
                               ev("Helm.ai", "funding", 70_500_000)])
    assert len(merged) == 1


def test_most_authoritative_source_is_kept():
    news = ev("Vitestro", "regulatory", date="2026-08-20", summary="a much longer news write-up of the clearance")
    fda = ev("Vitestro", "regulatory", date="2026-08-19", source_type="openfda", url="https://fda.gov/den250046")
    merged = merge_duplicates([news, fda])
    assert len(merged) == 1 and merged[0].source_url == "https://fda.gov/den250046"


def test_different_stories_about_one_company_stay_separate():
    events = [
        ev("Agility Robotics", "news", summary="investor day"),
        ev("Agility Robotics", "news", summary="Halos safety integration"),     # broad type: never merged on type
        ev("Koh Young", "regulatory", date="2026-09-10", product="Geniant Cranial"),
        ev("Koh Young", "regulatory", date="2026-09-12", product="Geniant Spine"),  # different product
        ev("SiMa.ai", amount=150_000_000, date="2026-10-01"),
        ev("SiMa.ai", amount=30_000_000, date="2026-10-02"),                      # different amount, still funding
        ev("Feather", "launch", date="2026-10-01"),
        ev("Feather", "launch", date="2026-10-20"),                               # weeks apart
        ev(None, "research", summary="paper one"),
        ev(None, "research", summary="paper two"),                                # no company: never merged
    ]
    assert len(merge_duplicates(events)) == 9  # only the two SiMa.ai funding events within a week merge


def test_repeat_of_an_already_stored_event_is_dropped():
    stored = [{"company": "Neura Robotics", "event_type": "funding", "funding_amount": 1_400_000_000,
               "date": "2026-06-20", "product": None}]
    events = [ev("NEURA Robotics GmbH", amount=1_400_000_000, date="2026-08-01", summary="still leads fundraising"),
              ev("Minerva Humanoids", amount=10_000_000, date="2026-10-06")]
    assert [e.company for e in merge_duplicates(events, stored)] == ["Minerva Humanoids"]
    assert same_story(stored[0], ev("Neura Robotics", amount=1_400_000_000).model_dump())  # no date counts as close


def test_extraction_prompt_has_the_funding_and_roundup_rules():
    prompt = _extract_request([], PROFILE)["prompt"]
    assert "Customer contracts" in prompt and "are not funding" in prompt
    assert "roundups" in prompt and "relevant=false" in prompt


def test_brief_is_given_the_largest_items_and_warns_when_it_skips_one(monkeypatch, caplog):
    prompts = []
    brief = "# Mecka AI raises\n\nMecka AI raised $60M ([TechCrunch](https://x.example/m))."
    monkeypatch.setattr("pipeline.llm.call_tool",
                        lambda prompt, tool, **k: prompts.append(prompt) or {"briefing_text": brief})
    events = [ev("Mecka AI", amount=60_000_000), ev("Humanoid", amount=152_000_000),
              ev("Vitestro", "regulatory", summary="De Novo"), ev("Small Co", "news")]
    with caplog.at_level(logging.WARNING):
        synthesis_agent.run_synthesis_agent(events, PROFILE)
    key_section = prompts[0].split("The largest deals")[1]
    assert key_section.index("Humanoid: $152M") < key_section.index("Mecka AI: $60M")  # biggest first
    assert "Vitestro" in key_section and "Small Co" not in key_section
    assert "doesn't mention key items: Humanoid, Vitestro" in caplog.text


def test_excerpt_keeps_the_first_sentences_within_a_limit():
    from pipeline.extraction_agent import EXCERPT_CHARS, excerpt
    text = "Humanoid raised $152M.  It is based in London! Total raised is $270M. A fourth sentence."
    assert excerpt(text) == "Humanoid raised $152M. It is based in London! Total raised is $270M."
    long = excerpt("word " * 400)
    assert len(long) <= EXCERPT_CHARS + 1 and long.endswith("…")
    assert excerpt("") is None and excerpt(None) is None


def test_extraction_attaches_the_source_excerpt(monkeypatch):
    from pipeline import extraction_agent
    from pipeline.models import RawResult
    raw = RawResult(title="t", url="https://x.example/h", snippet="Humanoid raised $152M. More text here.",
                    source="x.example", query_tag="funding:humanoids")
    monkeypatch.setattr(extraction_agent, "call_tools", lambda reqs: [{"events": [
        {"result_id": 0, "relevant": True, "company": "Humanoid", "event_type": "funding", "segment": "humanoids",
         "summary": "Humanoid raised $152M", "funding_amount": 152_000_000}]} for _ in reqs])
    (event,) = extraction_agent.run_extraction_agent([raw], PROFILE)
    assert event.excerpt == "Humanoid raised $152M. More text here."


def test_key_items_carry_source_text_and_the_brief_is_told_not_to_guess_dates(monkeypatch):
    prompts = []
    monkeypatch.setattr("pipeline.llm.call_tool",
                        lambda prompt, tool, **k: prompts.append(prompt) or {"briefing_text": "Humanoid"})
    event = ev("Humanoid", amount=152_000_000)
    event.excerpt = "Humanoid announced a $152M Series A at a $1.35B valuation."
    synthesis_agent.run_synthesis_agent([event], PROFILE)
    assert "Humanoid: $152M (funding, date not reported)" in prompts[0]
    assert 'Source text: "Humanoid announced a $152M Series A at a $1.35B valuation."' in prompts[0]
    assert "never guess one, and never use today's date" in prompts[0]
