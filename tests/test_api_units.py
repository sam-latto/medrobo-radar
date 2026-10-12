"""Offline tests for API helpers, pricing and usage recording (no database)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api import limits
from api.routes.events import source_name
from api.routes.industries import best_match, catalog_tree
from api.routes.jobs import all_role_types, resume_assignments
from api.usage import UsageCollector
from pipeline import llm
from pipeline.pricing import cost_usd


def test_cost_usd_uses_model_prices_and_cache_rates():
    # Sonnet 4.6: $3 in, $15 out, cache write 1.25x input, cache read 0.1x input
    assert cost_usd("claude-sonnet-4-6", 1_000_000, 0) == 3.0
    assert cost_usd("claude-sonnet-4-6", 0, 1_000_000) == 15.0
    assert cost_usd("claude-sonnet-4-6", cache_creation_tokens=1_000_000) == 3.75
    assert cost_usd("claude-sonnet-4-6", cache_read_tokens=1_000_000) == pytest.approx(0.30)
    assert cost_usd("claude-opus-5-5", cache_read_tokens=1_000_000) == 0.20  # listed cache-read price
    assert cost_usd("claude-sonnet-4-6", 2000, 500) == pytest.approx(0.0135)


def test_cost_usd_unknown_model_is_zero():
    assert cost_usd("some-future-model", 1000, 1000) == 0.0


def test_call_tool_reports_usage_to_sink(monkeypatch):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input={"ok": True})], stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0,
                              cache_read_input_tokens=5000))
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: response)
    monkeypatch.setattr(llm, "MODEL", "claude-sonnet-4-6")
    collector = UsageCollector()
    token = llm.usage_sink.set(collector.add)
    try:
        assert llm.call_tool("hi", {"name": "my_tool", "input_schema": {}}) == {"ok": True}
    finally:
        llm.usage_sink.reset(token)
    (record,) = collector.records
    assert record["purpose"] == "my_tool" and record["cache_read_tokens"] == 5000
    assert record["cost_usd"] == pytest.approx(cost_usd("claude-sonnet-4-6", 1000, 200, 0, 5000))


def test_call_tool_without_sink_records_nothing(monkeypatch):
    response = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={})], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: response)
    assert llm.call_tool("hi", {"name": "t", "input_schema": {}}) == {}


def test_period_bounds_day_and_month():
    now = datetime(2026, 12, 15, 18, 30, tzinfo=timezone.utc)
    assert limits.period_bounds("day", now) == (datetime(2026, 12, 15, tzinfo=timezone.utc),
                                                datetime(2026, 12, 16, tzinfo=timezone.utc))
    assert limits.period_bounds("month", now) == (datetime(2026, 12, 1, tzinfo=timezone.utc),
                                                  datetime(2027, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        limits.period_bounds("week", now)


def test_limit_messages_read_naturally():
    reset = datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert limits.limit_message("find_jobs", {"per": "day", "max": 1}, reset) == \
        "You've reached your limit of 1 job search per day. It resets tomorrow."
    assert limits.limit_message("tailor_resume", {"per": "month", "max": 30}, reset) == \
        "You've reached your limit of 30 resume tailorings per month. It resets on Jan 1."


def test_catalog_tree_nests_industries_under_sectors():
    rows = [
        {"id": "healthcare", "parent_id": None, "name": "Healthcare", "kind": "sector", "status": "active",
         "has_profile": False},
        {"id": "healthcare_robotics", "parent_id": "healthcare", "name": "Healthcare robotics",
         "kind": "industry", "status": "active", "has_profile": True},
        {"id": "consulting", "parent_id": None, "name": "Consulting", "kind": "industry",
         "status": "needs_review", "has_profile": False},
    ]
    tree = catalog_tree(rows)
    assert [n["id"] for n in tree] == ["healthcare", "consulting"]
    assert tree[0]["followable"] is False and tree[0]["industries"][0]["tracking_active"] is True
    assert tree[1]["followable"] is True and tree[1]["tracking_active"] is False and "industries" not in tree[1]


def test_best_match_by_shared_words():
    industries = [{"id": "healthcare_robotics", "name": "Healthcare robotics"},
                  {"id": "private_equity", "name": "Private equity"},
                  {"id": "physical_ai", "name": "Physical AI & humanoids"}]
    assert best_match("Surgical robotics", industries)["id"] == "healthcare_robotics"
    assert best_match("Healthcare PE / private equity", industries)["id"] == "private_equity"
    assert best_match("Underwater basket weaving", industries) is None


def test_resume_assignments_maps_role_types_and_defaults_to_newest():
    newest = {"id": "r2", "role_types": []}
    pm = {"id": "r1", "role_types": ["Product Management"]}
    pairs = resume_assignments([newest, pm], ["Product Management", "Strategy"])
    assert [(r["id"], t) for r, t in pairs] == [("r1", ["Product Management"]), ("r2", ["Strategy"])]
    # The newest resume already covers a role type: unmapped ones are added to it
    pairs = resume_assignments([pm], ["Product Management", "Strategy"])
    assert [(r["id"], t) for r, t in pairs] == [("r1", ["Product Management", "Strategy"])]
    assert resume_assignments([], ["Strategy"]) == []


def test_all_role_types_includes_custom_without_duplicates():
    prefs = {"role_types": ["Strategy"], "custom_role_types": "BizOps, Strategy , "}
    assert all_role_types(prefs) == ["Strategy", "BizOps"]


def test_source_names():
    assert source_name("openfda", "https://accessdata.fda.gov/x") == "FDA database"
    assert source_name("tavily", "https://www.globenewswire.com/news/1") == "globenewswire.com"
    assert source_name("tavily", "") == "Source"


def test_token_verifier_accepts_valid_and_rejects_bad_tokens():
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import ec

    from api.auth import TokenVerifier
    from api.errors import ApiError

    key = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())
    jwk_client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key()))
    verifier = TokenVerifier("https://proj.supabase.co", jwk_client=jwk_client)
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"sub": "11111111-1111-1111-1111-111111111111", "aud": "authenticated",
              "iss": "https://proj.supabase.co/auth/v1", "exp": now + 600}

    assert verifier.verify(jwt.encode(claims, key, algorithm="ES256"))["sub"] == claims["sub"]
    for bad in (jwt.encode({**claims, "exp": now - 10}, key, algorithm="ES256"),       # expired
                jwt.encode({**claims, "aud": "anon"}, key, algorithm="ES256"),         # wrong audience
                jwt.encode({**claims, "iss": "https://evil/auth/v1"}, key, algorithm="ES256"),
                jwt.encode(claims, other, algorithm="ES256"),                          # wrong key
                "not-a-token"):
        with pytest.raises(ApiError) as exc:
            verifier.verify(bad)
        assert exc.value.status == 401


# ── Message Batches ──────────────────────────────────────────────────────────

def _tool_message(data, input_tokens=1000, output_tokens=100):
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=data)], stop_reason="tool_use",
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))


class FakeBatches:
    def __init__(self, results, status_after=1):
        self.results_list, self.polls, self.status_after = results, 0, status_after
        self.created, self.cancelled = None, False

    def create(self, requests):
        self.created = requests
        return SimpleNamespace(id="b1", processing_status="in_progress")

    def retrieve(self, batch_id):
        self.polls += 1
        return SimpleNamespace(id=batch_id, processing_status="ended" if self.polls >= self.status_after
                               else "in_progress")

    def cancel(self, batch_id):
        self.cancelled = True

    def results(self, batch_id):
        return self.results_list


def _batch_result(custom_id, data=None, kind="succeeded"):
    result = SimpleNamespace(type=kind, message=_tool_message(data) if data is not None else None)
    return SimpleNamespace(custom_id=custom_id, result=result)


@pytest.fixture
def batch_env(monkeypatch):
    monkeypatch.setattr(llm, "BATCH_POLL_SECONDS", 0)
    monkeypatch.setattr(llm, "MODEL", "claude-sonnet-4-6")
    collector = UsageCollector()
    tokens = [llm.batch_mode.set(True), llm.usage_sink.set(collector.add)]
    yield collector
    llm.usage_sink.reset(tokens[1])
    llm.batch_mode.reset(tokens[0])


def test_call_tools_batches_requests_and_records_half_price(monkeypatch, batch_env):
    tool = {"name": "t", "input_schema": {}}
    fake = FakeBatches([_batch_result("r1", {"n": 1}), _batch_result("r0", {"n": 0}),
                        _batch_result("r2", kind="errored")])
    monkeypatch.setattr(llm.client.messages, "batches", fake)
    out = llm.call_tools([{"prompt": f"p{i}", "tool": tool, "model": "claude-haiku-5-5" if i == 0 else None}
                          for i in range(3)])
    assert out[0] == {"n": 0} and out[1] == {"n": 1} and isinstance(out[2], Exception)
    assert [r["params"]["model"] for r in fake.created] == ["claude-haiku-5-5", "claude-sonnet-4-6",
                                                            "claude-sonnet-4-6"]
    costs = {r["model"]: r["cost_usd"] for r in batch_env.records}
    assert costs["claude-haiku-5-5"] == pytest.approx(0.5 * cost_usd("claude-haiku-5-5", 1000, 100))
    assert costs["claude-sonnet-4-6"] == pytest.approx(0.5 * cost_usd("claude-sonnet-4-6", 1000, 100))


def test_call_tools_falls_back_to_direct_calls(monkeypatch, batch_env):
    tool = {"name": "t", "input_schema": {}}

    class Broken:
        def create(self, requests):
            raise RuntimeError("batches unavailable")
    monkeypatch.setattr(llm.client.messages, "batches", Broken())
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: _tool_message({"direct": True}))
    assert llm.call_tools([{"prompt": "p", "tool": tool}]) == [{"direct": True}]
    assert batch_env.records[0]["cost_usd"] == pytest.approx(cost_usd("claude-sonnet-4-6", 1000, 100))


def test_call_tools_cancels_slow_batch_and_falls_back(monkeypatch, batch_env):
    tool = {"name": "t", "input_schema": {}}
    fake = FakeBatches([], status_after=10**9)
    monkeypatch.setattr(llm.client.messages, "batches", fake)
    monkeypatch.setattr(llm, "BATCH_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: _tool_message({"direct": True}))
    assert llm.call_tools([{"prompt": "p", "tool": tool}]) == [{"direct": True}]
    assert fake.cancelled


def test_call_tools_without_batch_mode_calls_directly(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "call_tool", lambda prompt, tool, **k: seen.append(k.get("model")) or {"ok": prompt})
    assert llm.call_tools([{"prompt": "a", "tool": {}, "model": "m1"}, {"prompt": "b", "tool": {}}]) == \
        [{"ok": "a"}, {"ok": "b"}]
    assert seen == ["m1", None]


def test_extraction_uses_the_cheaper_model(monkeypatch):
    from pipeline import extraction_agent
    from pipeline.models import RawResult
    from profiles.loader import load_profile
    seen = []
    monkeypatch.setattr(llm, "call_tool", lambda prompt, tool, **k: seen.append(k.get("model")) or {"events": []})
    raw = [RawResult(title="t", url="https://x.com/1", snippet="s", source="x.com", query_tag="news:other")]
    extraction_agent.run_extraction_agent(raw, load_profile("healthcare_robotics"))
    assert seen == [extraction_agent.EXTRACTION_MODEL] and seen[0] == "claude-haiku-5-5"


# ── Focus filtering and weekly cadence ───────────────────────────────────────

def test_focus_stems_drop_words_the_industry_shares():
    from api import focus
    assert focus.focus_stems("Surgical robotics", "Healthcare robotics") == ["surg"]
    assert focus.focus_stems("Exoskeletons and rehab", "Healthcare robotics") == ["exoskele", "rehab"]
    assert focus.focus_stems("Robotics", "Healthcare robotics") == []
    assert focus.focus_stems(None, "x") == []


def test_focus_matches_word_starts():
    from api import focus
    stems = focus.focus_stems("Surgical robotics", "Healthcare robotics")
    assert focus.matches({"summary": "First procedure with a surgery robot"}, stems)
    assert focus.matches({"segment": "surgical", "summary": "x"}, stems)
    assert not focus.matches({"summary": "A resurgence in rehab funding"}, stems)  # not a word start
    assert focus.sql_pattern(["surg", "c++"]) == r"\m(surg|c\+\+)"


def test_low_follower_industries_run_weekly():
    from datetime import date as d
    from worker.run_daily import due_today
    today = d(2026, 12, 10)
    assert due_today({"followers": 5, "last_run_date": today}, today)            # popular: daily
    assert due_today({"followers": 1, "last_run_date": None}, today)             # first run: now
    assert not due_today({"followers": 1, "last_run_date": d(2026, 12, 5)}, today)
    assert due_today({"followers": 2, "last_run_date": d(2026, 12, 3)}, today)   # a week later
