"""Offline tests for fixes from the Phase B-D code reviews (no database, no network)."""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.usage import UsageCollector
from pipeline import llm
from pipeline.pricing import cost_usd, search_cost_usd

ROOT = Path(__file__).resolve().parent.parent


# ── Search cost recording ────────────────────────────────────────────────────

def test_search_cost_by_depth():
    assert search_cost_usd("advanced") == pytest.approx(2 * search_cost_usd("basic"))


def test_tavily_source_records_successful_searches_only(monkeypatch):
    from profiles.loader import load_profile
    from sources import tavily_source

    calls = []

    class FakeTavily:
        def __init__(self, api_key):
            pass

        def search(self, **kw):
            calls.append(kw["query"])
            if len(calls) == 2:
                raise RuntimeError("rate limited")
            return {"results": []}

    monkeypatch.setattr(tavily_source, "TavilyClient", FakeTavily)
    collector = UsageCollector()
    token = llm.usage_sink.set(collector.add)
    try:
        tavily_source.TavilySource().fetch(load_profile("healthcare_robotics"), None)
    finally:
        llm.usage_sink.reset(token)
    assert len(calls) > 2
    assert len(collector.records) == len(calls) - 1  # the failed search isn't charged
    record = collector.records[0]
    assert record["kind"] == "search" and record["model"] == "tavily:advanced"
    assert record["cost_usd"] == search_cost_usd("advanced")


def test_usage_flush_names_actions_and_counts_cache_tokens():
    class Cursor:
        def __init__(self, sink):
            self.sink = sink

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, rows):
            self.sink.extend(rows)

    class Conn:
        def __init__(self):
            self.rows = []

        def transaction(self):
            return Cursor(self.rows)

        def cursor(self):
            return Cursor(self.rows)

    collector = UsageCollector()
    collector.add({"purpose": "score_jobs", "model": "m", "input_tokens": 10, "output_tokens": 5,
                   "cache_creation_tokens": 2, "cache_read_tokens": 3, "cost_usd": 0.1})
    collector.add({"purpose": "job_search", "model": "tavily:advanced", "input_tokens": 0, "output_tokens": 0,
                   "cache_creation_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.016, "kind": "search"})
    conn = Conn()
    assert collector.flush(conn, "user-1", "physical_ai") == 2
    assert [r[2] for r in conn.rows] == ["llm:score_jobs", "search:job_search"]
    assert conn.rows[0][4] == 15  # input + cache write + cache read tokens
    assert collector.records == []


def test_a_failing_usage_sink_never_breaks_the_call(monkeypatch):
    response = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"ok": 1})], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: response)

    def broken(record):
        raise RuntimeError("database down")
    token = llm.usage_sink.set(broken)
    try:
        assert llm.call_tool("p", {"name": "t", "input_schema": {}}) == {"ok": 1}
    finally:
        llm.usage_sink.reset(token)


# ── Threads see the store and the cost recorder ──────────────────────────────

def test_collector_threads_see_the_current_store():
    from database.store import store, use_store
    from jobs import collector
    sentinel = object()
    with use_store(sentinel):
        results = [r for _, r in collector._parallel(lambda _: store(), [1, 2, 3, 4])]
    assert results == [sentinel] * 4


class FakeMatchStore:
    def __init__(self, fail_on=None):
        self.saved, self.fail_on = [], fail_on

    def get_matches(self, resume_id):
        return {}

    def save_match(self, job_id, *a):
        if job_id == self.fail_on:
            raise RuntimeError("resume was deleted")
        self.saved.append(job_id)

    def update_posting_company(self, *a):
        pass


def _jobs(n):
    return [{"id": i, "title": f"Product Manager {i}", "company": "Acme", "location": "Boston",
             "provider": "greenhouse", "description": "d"} for i in range(n)]


def test_scoring_threads_record_claude_cost(monkeypatch):
    from jobs import matcher

    def fake_create(**kw):
        prompt = kw["messages"][0]["content"]
        ids = [int(i) for i in re.findall(r"\[job_id=(\d+)\]", prompt)]
        data = {"scores": [{"job_id": i, "score": 80, "summary": "s", "strengths": [], "gaps": [],
                            "level_fit": "match", "industry_match": True} for i in ids]}
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=data)], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))
    monkeypatch.setattr(llm.client.messages, "create", fake_create)
    collector = UsageCollector()
    token = llm.usage_sink.set(collector.add)
    try:
        store = FakeMatchStore(fail_on=3)
        scored = matcher.score_jobs({"id": "r1", "text": "resume"}, _jobs(20), ["X"], store=store)
    finally:
        llm.usage_sink.reset(token)
    batches = -(-20 // matcher.SCORE_BATCH_SIZE)
    assert len(collector.records) == batches  # every worker thread's call was recorded
    assert scored == 19 and 3 not in store.saved  # one bad row doesn't lose the rest


def test_scoring_raises_when_every_batch_fails(monkeypatch):
    from jobs import matcher
    monkeypatch.setattr(matcher, "call_tool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError, match="every scoring batch failed"):
        matcher.score_jobs({"id": "r1", "text": "resume"}, _jobs(10), ["X"], store=FakeMatchStore())


def test_role_type_keywords_resolved_once_per_search(monkeypatch):
    from jobs import functions, matcher
    lookups = []
    real = functions.keywords_for
    monkeypatch.setattr(functions, "keywords_for", lambda f: lookups.append(f) or real(f))
    matcher.candidate_jobs(["x"], ["Product Management", "Strategy"], [], postings=_jobs(200))
    assert sorted(lookups) == ["Product Management", "Strategy"]


# ── Model routing ────────────────────────────────────────────────────────────

def test_extraction_uses_the_configured_cheaper_model(monkeypatch):
    from pipeline import extraction_agent
    from pipeline.models import RawResult
    from profiles.loader import load_profile
    monkeypatch.setattr(extraction_agent, "EXTRACTION_MODEL", "cheap-model-sentinel")
    seen = []
    monkeypatch.setattr(llm, "call_tool", lambda prompt, tool, **k: seen.append(k.get("model")) or {"events": []})
    raw = [RawResult(title="t", url="https://x.com/1", snippet="s", source="x.com", query_tag="news:other")]
    extraction_agent.run_extraction_agent(raw, load_profile("healthcare_robotics"))
    assert seen == ["cheap-model-sentinel"]


def test_company_matching_uses_cheaper_model_and_brief_uses_main_model(monkeypatch):
    from pipeline import companies, synthesis_agent
    from pipeline.models import ExtractedEvent
    from profiles.loader import load_profile
    seen = {}
    monkeypatch.setattr(companies, "call_tool",
                        lambda prompt, tool, **k: seen.setdefault("match", k.get("model")) and {"matches": []})
    companies._ask_claude_to_match([("Acme Robotics", [{"id": 1, "name": "Acme Robotic"}])])
    assert seen["match"] == companies.EXTRACTION_MODEL

    monkeypatch.setattr(llm, "call_tool", lambda prompt, tool, **k: seen.setdefault("brief", k.get("model")) or
                        {"briefing_text": "# Hi", "additional_notable_indices": []})
    synthesis_agent.run_synthesis_agent(
        [ExtractedEvent(event_type="news", segment="other", summary="s", source_url="u")],
        load_profile("healthcare_robotics"))
    assert seen["brief"] is None  # None means ANTHROPIC_MODEL


# ── Email safety ─────────────────────────────────────────────────────────────

def test_brief_links_cannot_break_out_of_href():
    from pipeline.text_utils import markdown_to_email_html
    html = markdown_to_email_html('[Read](https://x.example"style="position:fixed)')
    assert "<a " not in html  # not turned into a link; the text stays escaped, harmless text
    ok = markdown_to_email_html("[Read](https://x.example/a?b=1&c=2)")
    assert '<a href="https://x.example/a?b=1&amp;c=2"' in ok


def test_safe_url_only_allows_http():
    from email_digest.sender import safe_url
    assert safe_url("javascript:alert(1)") == "#" and safe_url("data:text/html,x") == "#"
    assert safe_url(None) == "#"
    assert safe_url('https://a.com/?q="x"') == "https://a.com/?q=&quot;x&quot;"


def test_user_digest_reaches_the_user_and_escapes_focus(monkeypatch):
    from email_digest import sender
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, from_, to, body):
            sent["to"], sent["body"] = to, body

    monkeypatch.setattr(sender.smtplib, "SMTP", FakeSMTP)
    for name, value in {"EMAIL_FROM": "radar@example.com", "EMAIL_TO": "owner@example.com",
                        "SENDGRID_API_KEY": "", "SMTP_USER": "u", "SMTP_PASSWORD": "p"}.items():
        monkeypatch.setattr(sender, name, value)
    section = {"industry_name": "Physical AI", "briefing_text": "# Hi", "focus_label": "<b>Humanoids",
               "focus_events": []}
    assert sender.send_user_digest("student@umich.edu", [section], "2026-12-01") is True
    assert sent["to"] == ["student@umich.edu"]
    import email
    message = email.message_from_string(sent["body"])
    body = "".join(part.get_payload(decode=True).decode() for part in message.walk()
                   if part.get_content_type() == "text/html")
    assert "&lt;b&gt;Humanoids" in body and "<b>Humanoids" not in body
    assert "Nothing specific to your focus today." in body
    assert sender.send_user_digest("student@umich.edu", [], "2026-12-01") is False


def test_alerts_never_raise(monkeypatch):
    from email_digest import sender
    monkeypatch.setattr(sender, "EMAIL_FROM", "radar@example.com")
    monkeypatch.setattr(sender, "EMAIL_TO", "owner@example.com")
    monkeypatch.setattr(sender, "_send", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    sender.send_admin_alert("subject", "<p>x</p>")
    sender.send_failure_alert([("physical_ai", "boom")])


# ── API helpers ──────────────────────────────────────────────────────────────

def test_csv_cells_cannot_be_formulas():
    from api.routes.events import csv_safe
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_safe("-5 drop") == "'-5 drop" and csv_safe("@x") == "'@x"
    assert csv_safe("Normal text") == "Normal text" and csv_safe(None) == "" and csv_safe(12) == "12"


def test_source_errors_drop_query_strings():
    from pipeline.search_agent import redact_urls
    text = "500 Server Error for url: https://api.fda.gov/device/510k.json?search=x&api_key=SECRET"
    assert redact_urls(text) == "500 Server Error for url: https://api.fda.gov/device/510k.json?[redacted]"


def test_focus_acronyms_are_whole_words_and_terms_are_capped():
    from api import focus
    stems = focus.focus_stems("AI chips", "Semiconductors")
    assert stems == ["=ai", "chips"]
    assert focus.matches({"summary": "An AI accelerator"}, stems)
    assert not focus.matches({"summary": "Federal aid for fabs"}, ["=ai"])
    assert focus.sql_pattern(["=ai", "surg"]) == r"\m(ai\M|surg)"
    many = "apple banana cherry damson elder figs grape hazel iris juniper kiwi lemon"
    assert len(focus.focus_stems(many, "x")) == focus.MAX_STEMS


def test_supabase_admin_requests(monkeypatch):
    from api import supabase_admin
    calls = []

    class Resp:
        def __init__(self, status=200, body=None):
            self.status_code, self._body = status, body or []

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return self._body

    def fake_post(url, **kw):
        calls.append(("post", url, kw.get("json")))
        return Resp(body=[{"name": "a.pdf"}, {"name": "b.pdf"}, {}])

    def fake_delete(url, **kw):
        calls.append(("delete", url, kw.get("json")))
        return Resp(404 if "/auth/" in url else 200)

    monkeypatch.setattr(supabase_admin.requests, "post", fake_post)
    monkeypatch.setattr(supabase_admin.requests, "delete", fake_delete)
    admin = supabase_admin.SupabaseAdmin("https://proj.supabase.co/", "sb_secret_x")
    assert admin.list_user_files("u1") == ["u1/a.pdf", "u1/b.pdf"]
    admin.delete_files([])
    admin.delete_files(["u1/a.pdf"])
    admin.delete_user("u1")  # a 404 (already gone) is fine
    assert calls == [
        ("post", "https://proj.supabase.co/storage/v1/object/list/resumes", {"prefix": "u1", "limit": 1000}),
        ("delete", "https://proj.supabase.co/storage/v1/object/resumes", {"prefixes": ["u1/a.pdf"]}),
        ("delete", "https://proj.supabase.co/auth/v1/admin/users/u1", None),
    ]


# ── Scripts and worker entry point ───────────────────────────────────────────

def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_db_migrate_splits_versions_in_order():
    files = _load_script("db_migrate")._migration_files()
    versions = [v for v, _, _ in files]
    assert versions == sorted(versions) and versions[0] == "20261011000001"
    assert [n for _, n, _ in files][:2] == ["init", "limits"]


def test_worker_exit_code(monkeypatch):
    from contextlib import nullcontext
    from worker import run_daily
    monkeypatch.setattr(run_daily, "connect", lambda: nullcontext(object()))
    for failed, industries, code in [(["a", "b"], ["a", "b"], 1), (["a"], ["a", "b"], 0), ([], [], 0)]:
        monkeypatch.setattr(run_daily, "run", lambda *a, f=failed, i=industries: {"failed": f, "industries": i})
        assert run_daily.main([]) == code
