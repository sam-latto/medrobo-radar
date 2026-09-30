"""Company momentum: a weighted score of recent activity over a rolling window."""
import math
from datetime import date, timedelta

from database import companies as company_db
from database.db import get_connection
from pipeline.companies import normalize_name
from profiles.loader import IndustryProfile

WINDOW_DAYS = 90

DEFAULT_WEIGHTS = {
    "funding": 3.0,          # scaled by log10 of the amount (see _funding_points)
    "regulatory": 3.0,
    "partnership_ma": 2.0,
    "launch": 2.0,
    "deployment": 2.0,
    "earnings": 1.5,
    "research": 1.0,
    "news": 0.5,             # news volume: lowest weight
}
DEFAULT_WEIGHT_OTHER = 1.0


def weights_for(profile: IndustryProfile) -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **profile.momentum_weights}


def _funding_points(amount: int | None) -> float:
    """1x for undisclosed or <= $1M, +1x per 10x: $10M=2x, $100M=3x, $1B=4x."""
    if not amount or amount <= 1_000_000:
        return 1.0
    return 1.0 + math.log10(amount / 1_000_000)


def _dedup_key(e: dict) -> tuple:
    """The same real-world event is often reported by several outlets; count it once."""
    et = e["event_type"]
    if et == "news":
        return ("news", e["id"])  # news volume is the signal
    if e.get("funding_amount"):
        return (et, e["funding_amount"])
    if e.get("product"):
        return (et, e["product"].strip().lower())
    if e.get("regulatory_status"):
        return (et, e["regulatory_status"].strip().lower(), (e.get("date") or "")[:7])
    return (et, e["id"])


def score_events(events: list[dict], weights: dict[str, float]) -> float:
    seen: set[tuple] = set()
    score = 0.0
    for e in events:
        key = _dedup_key(e)
        if key in seen:
            continue
        seen.add(key)
        w = weights.get(e["event_type"], DEFAULT_WEIGHT_OTHER)
        if e["event_type"] == "funding":
            w *= _funding_points(e.get("funding_amount"))
        score += w
    return round(score, 2)


def rank_companies(profile: IndustryProfile, as_of: date | None = None,
                   window_days: int = WINDOW_DAYS) -> list[dict]:
    """Momentum ranking for one industry. Includes every company linked to the
    industry (watchlist companies too, even with no recent events)."""
    as_of = as_of or date.today()
    since = (as_of - timedelta(days=window_days)).isoformat()
    weights = weights_for(profile)

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, company_id, event_type, funding_amount, product, regulatory_status, date
               FROM events
               WHERE industry_id = ? AND company_id IS NOT NULL AND date >= ? AND date <= ?""",
            (profile.id, since, as_of.isoformat()),
        ).fetchall()
    by_company: dict[int, list[dict]] = {}
    for r in rows:
        by_company.setdefault(r["company_id"], []).append(dict(r))

    watch_norm = {normalize_name(n) for n in profile.watchlist}

    ranking = []
    for c in company_db.list_companies(profile.id):
        events = by_company.get(c["id"], [])
        funding_seen: set[int] = set()
        disclosed = 0
        for e in events:
            amt = e.get("funding_amount")
            if e["event_type"] == "funding" and amt and amt not in funding_seen:
                funding_seen.add(amt)
                disclosed += amt
        ranking.append({
            "company_id": c["id"],
            "company": c["name"],
            "watchlist": c["normalized_name"] in watch_norm,
            "score": score_events(events, weights),
            "events_90d": len(events),
            "funding_90d": disclosed,
            "regulatory_90d": sum(1 for e in events if e["event_type"] == "regulatory"),
            "last_event": max((e["date"] for e in events if e.get("date")), default=None),
        })
    ranking.sort(key=lambda r: (-r["score"], not r["watchlist"], r["company"].lower()))
    for i, r in enumerate(ranking, 1):
        r["rank"] = i
    return ranking
