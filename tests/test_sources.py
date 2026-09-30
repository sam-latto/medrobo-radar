"""Source plug-ins parse saved sample responses (no live calls)."""
import json
from datetime import date
from pathlib import Path

import pytest

from profiles.loader import load_profile
from sources import http
from sources.openfda_source import OpenFDA510kSource, build_search, parse_results
from sources.registry import SOURCES
from sources.research_sources import (
    ArxivSource,
    parse_arxiv_feed,
    parse_pubmed_summaries,
    parse_trials,
)
from sources.sec_form_d_source import (
    SecFormDSource,
    filing_to_result,
    parse_primary_doc,
    parse_search_hits,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEALTH = load_profile("healthcare_robotics")
PAI = load_profile("physical_ai")


def _json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_every_profile_source_is_registered():
    from profiles.loader import load_profiles
    for profile in load_profiles():
        for name in profile.sources:
            assert name in SOURCES, f"{profile.id} uses unknown source {name}"


def test_openfda_search_string():
    s = build_search(["robotic", "robotically-assisted"], ["OLO"], date(2026, 8, 1), date(2026, 9, 29))
    assert s == ('decision_date:[20260801+TO+20260929]+AND+(device_name:robotic'
                 '+OR+device_name:"robotically-assisted"+OR+product_code:OLO)')


def test_openfda_parse():
    results = parse_results(_json("openfda_510k.json"), HEALTH)
    assert len(results) == 3
    k510, de_novo = results[0], results[1]
    assert k510.source_type == "openfda"
    assert k510.url.startswith("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID=K")
    assert k510.prefilled["regulatory_status"] == "FDA 510(k) Cleared"
    assert k510.prefilled["event_type"] == "regulatory"
    assert k510.prefilled["company"] == "Koh Young Technology, Inc."
    assert k510.prefilled["date"] == "2026-09-10"
    assert "denovo.cfm?ID=DEN" in de_novo.url
    assert de_novo.prefilled["regulatory_status"] == "FDA De Novo Granted"


def test_openfda_regulatory_maps_to_news_when_profile_lacks_it():
    results = parse_results(_json("openfda_510k.json"), PAI)
    assert results[0].prefilled["event_type"] == "news"


def test_sec_parse_search_hits_skips_amendments():
    filings = parse_search_hits(_json("sec_efts_search.json"))
    assert filings and all(f["adsh"] and f["cik"] for f in filings)
    assert "0002138321-26-000004" not in {f["adsh"] for f in filings}  # the D/A in the fixture
    assert filings[0]["name"] == "Salem Robotics, Inc."


def test_sec_primary_doc_to_result():
    doc = parse_primary_doc((FIXTURES / "sec_form_d_primary_doc.xml").read_text(encoding="utf-8"))
    assert doc["industry_group"] == "Other Health Care"
    assert doc["total_amount_sold"] == 32_500_000
    filing = {"adsh": "0009999999-26-000001", "cik": "9999999", "name": "x", "file_date": "2026-09-01"}
    r = filing_to_result(filing, doc, HEALTH)
    assert r.source_type == "sec_form_d"
    assert r.prefilled["funding_amount"] == 32_500_000
    assert r.prefilled["company"] == "Example Surgical Robotics, Inc."
    assert r.prefilled["event_type"] == "funding"
    assert r.url == "https://www.sec.gov/Archives/edgar/data/9999999/000999999926000001/0009999999-26-000001-index.htm"


def test_sec_fetch_filters_groups(monkeypatch):
    import sources.sec_form_d_source as sec

    class Resp:
        def __init__(self, payload=None, text=""):
            self._payload, self.text = payload, text

        def json(self):
            return self._payload

    doc_xml = (FIXTURES / "sec_form_d_primary_doc.xml").read_text(encoding="utf-8")

    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers and headers["User-Agent"] == "Test Agent test@example.com"
        if "search-index" in url:
            return Resp(_json("sec_efts_search.json"))
        return Resp(text=doc_xml)

    monkeypatch.setattr(sec, "SEC_USER_AGENT", "Test Agent test@example.com")
    monkeypatch.setattr(sec.http, "get", fake_get)
    profile = HEALTH.model_copy(deep=True)
    profile.sources["sec_form_d"] = {"keywords": ["robotics"], "industry_groups": ["Other Health Care"]}
    assert len(SecFormDSource().fetch(profile, date(2026, 9, 1))) == 3
    profile.sources["sec_form_d"] = {"keywords": ["robotics"], "industry_groups": ["Biotechnology"]}
    assert SecFormDSource().fetch(profile, date(2026, 9, 1)) == []


def test_sec_requires_user_agent(monkeypatch):
    import sources.sec_form_d_source as sec
    monkeypatch.setattr(sec, "SEC_USER_AGENT", "")
    monkeypatch.setattr(sec.http, "get", lambda *a, **k: pytest.fail("must not call SEC without a User-Agent"))
    assert SecFormDSource().fetch(HEALTH, date(2026, 9, 1)) == []


def test_pubmed_parse():
    results = parse_pubmed_summaries(_json("pubmed_esummary.json"), HEALTH)
    assert len(results) == 3
    r = results[0]
    assert r.url == "https://pubmed.ncbi.nlm.nih.gov/42809743/"
    assert r.date == "2026-09-28"
    assert r.prefilled["event_type"] == "research"
    assert r.source_type == "pubmed"


def test_arxiv_parse_filters_by_date():
    xml = (FIXTURES / "arxiv.xml").read_text(encoding="utf-8")
    results = parse_arxiv_feed(xml, PAI, since=date(2026, 9, 1))
    assert results and results[0].url.startswith("https://arxiv.org/abs/")
    assert results[0].title == "Humanoid Loco-Manipulation With Discrete VLA Model"
    assert results[0].prefilled["event_type"] == "research"
    assert parse_arxiv_feed(xml, PAI, since=date(2026, 10, 1)) == []


def test_arxiv_fetch_uses_profile_query(monkeypatch):
    seen = {}

    class Resp:
        text = (FIXTURES / "arxiv.xml").read_text(encoding="utf-8")

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params)
        return Resp()

    monkeypatch.setattr(http, "get", fake_get)
    results = ArxivSource().fetch(PAI, date(2026, 9, 1))
    assert seen["search_query"] == PAI.sources["arxiv"]["query"]
    assert results


def test_clinicaltrials_parse():
    results = parse_trials(_json("clinicaltrials.json"), HEALTH)
    assert results[0].url == "https://clinicaltrials.gov/study/NCT04073706"
    assert "company" not in results[0].prefilled  # academic sponsor, not a company
    assert results[0].source_type == "clinicaltrials"
    data = _json("clinicaltrials.json")
    data["studies"][0]["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]["class"] = "INDUSTRY"
    assert parse_trials(data, HEALTH)[0].prefilled["company"] == "Queensland Centre for Gynaecological Cancer"


def test_openfda_fetch_404_means_no_results(monkeypatch):
    import requests

    def fake_get(url, **kwargs):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(http, "get", fake_get)
    assert OpenFDA510kSource().fetch(HEALTH, date(2026, 9, 1)) == []


# ── Pipeline wiring ──────────────────────────────────────────────────────────

def _raw(i, **kw):
    from pipeline.models import RawResult
    base = dict(title=f"T{i}", url=f"https://ex.com/{i}", snippet="s", source="ex.com",
                date="2026-09-01", query_tag="news:other")
    return RawResult(**{**base, **kw})


def test_extraction_prefers_primary_facts_and_drops_off_topic(monkeypatch):
    from pipeline import extraction_agent

    fda = _raw(0, source_type="openfda", prefilled={
        "company": "THINK Surgical, Inc.", "event_type": "regulatory", "date": "2026-07-24",
        "regulatory_status": "FDA 510(k) Cleared", "product": "TMINI"})
    form_d = _raw(1, source_type="sec_form_d", prefilled={
        "company": "Example Surgical Robotics, Inc.", "event_type": "funding", "funding_amount": 32_500_000})
    news = _raw(2)
    prompts = []

    def fake_call_tool(prompt, tool, max_tokens=4096, system=None):
        prompts.append(prompt)
        return {"events": [
            # model gets the amount/company wrong; primary facts must win
            {"result_id": 0, "relevant": True, "company": "Think", "event_type": "launch",
             "segment": "surgical", "summary": "TMINI cleared"},
            {"result_id": 2, "relevant": False, "event_type": "news", "segment": "other", "summary": "off-topic"},
            # result 1 omitted entirely -> built from its prefilled facts
        ]}

    monkeypatch.setattr(extraction_agent, "call_tool", fake_call_tool)
    events = {e.source_url: e for e in extraction_agent.run_extraction_agent([fda, form_d, news], HEALTH)}

    assert "Authoritative facts from the primary source" in prompts[0]
    assert set(events) == {fda.url, form_d.url}
    e = events[fda.url]
    assert (e.company, e.event_type, e.date, e.regulatory_status, e.segment) == (
        "THINK Surgical, Inc.", "regulatory", "2026-07-24", "FDA 510(k) Cleared", "surgical")
    assert e.source_type == "openfda"
    f = events[form_d.url]
    assert (f.funding_amount, f.event_type, f.source_type) == (32_500_000, "funding", "sec_form_d")


def test_search_agent_runs_sources_and_dedups(monkeypatch):
    from pipeline import search_agent

    class Fake:
        def __init__(self, name, results=None, fail=False):
            self.name, self.source_type, self.default_lookback_days = name, name, 7
            self._results, self._fail = results or [], fail

        def fetch(self, profile, since):
            if self._fail:
                raise RuntimeError("boom")
            return self._results

    fakes = {
        "tavily": Fake("tavily", [_raw(1), _raw(2)]),
        "openfda_510k": Fake("openfda_510k", [_raw(2), _raw(3)]),  # url 2 duplicates tavily
        "sec_form_d": Fake("sec_form_d", fail=True),
    }
    monkeypatch.setattr(search_agent, "get_source", fakes.get)
    monkeypatch.setattr(search_agent, "url_exists", lambda url, ind: url.endswith("/3"))
    results, counts = search_agent.run_search_agent(HEALTH)

    assert [r.url for r in results] == ["https://ex.com/1", "https://ex.com/2"]
    assert counts["tavily"] == {"fetched": 2, "new": 2}
    assert counts["openfda_510k"] == {"fetched": 2, "new": 0}
    assert "boom" in counts["sec_form_d"]["error"]
    assert counts["pubmed"]["error"] == "unknown source"  # not in the fake registry


def test_profile_agent_offers_every_source():
    from pipeline.profile_agent import build_profile_tool
    offered = build_profile_tool()["input_schema"]["properties"]["sources"]["properties"]
    assert set(offered) == set(SOURCES)
