"""Personalization by filtering: a user's focus within an industry (e.g. "Surgical robotics"
inside Healthcare robotics) highlights matching events. Briefs stay shared; no Claude calls.
"""
import re

GENERIC = {"and", "the", "for", "industry", "industries", "companies", "company", "market", "markets",
           "tech", "technology", "technologies", "services", "sector"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


MAX_STEMS = 8


def focus_stems(focus: str | None, industry_name: str = "") -> list[str]:
    """Terms that identify the focus, minus words the whole industry shares (at most MAX_STEMS).

    Longer words become stems matched at the start of a word: 'Surgical robotics' in
    'Healthcare robotics' -> ['surg'] (surgical, surgery, surgeon). Short acronyms written in
    capitals ('AI', 'EV', '3D') are kept as whole words, marked with a leading '='."""
    if not focus:
        return []
    shared = set(_words(industry_name)) | GENERIC
    stems = []
    for token in re.findall(r"[A-Za-z0-9]+", focus):
        w = token.lower()
        if w in shared:
            continue
        if len(w) == 2 and token.upper() == token:
            stem = "=" + w
        elif len(w) < 3:
            continue
        else:
            stem = w if len(w) <= 5 else w[:max(4, len(w) - 4)]
        if stem not in stems:
            stems.append(stem)
    return stems[:MAX_STEMS]


def sql_pattern(stems: list[str]) -> str | None:
    """Postgres regex: any stem at the start of a word, or an acronym as a whole word."""
    if not stems:
        return None
    parts = [re.escape(s[1:]) + r"\M" if s.startswith("=") else re.escape(s) for s in stems]
    return r"\m(" + "|".join(parts) + ")"


def matches(event: dict, stems: list[str]) -> bool:
    text = " ".join(str(event.get(k) or "") for k in ("summary", "company", "product", "segment"))
    words = _words(text.replace("_", " "))
    return any((w == s[1:]) if s.startswith("=") else w.startswith(s) for s in stems for w in words)


def user_focuses(conn, user_id) -> dict[str, dict]:
    """{industry_id: {"label", "stems", "pattern"}} for the user's subscriptions with a focus."""
    rows = conn.execute(
        """select s.industry_id, s.focus, i.name from public.subscriptions s
           join public.industries i on i.id = s.industry_id
           where s.user_id = %s and s.focus is not null and s.focus <> ''""", (user_id,)).fetchall()
    out = {}
    for r in rows:
        stems = focus_stems(r["focus"], r["name"])
        if stems:
            out[r["industry_id"]] = {"label": r["focus"], "stems": stems, "pattern": sql_pattern(stems)}
    return out


def focus_sql(focuses: dict[str, dict], params: dict, alias: str = "e") -> str:
    """SQL boolean: the event matches the user's focus for its industry. Adds params."""
    if not focuses:
        return "false"
    text = (f"coalesce({alias}.summary, '') || ' ' || coalesce({alias}.company, '') || ' ' || "
            f"coalesce({alias}.product, '') || ' ' || replace({alias}.segment, '_', ' ')")
    parts = []
    for n, (industry_id, f) in enumerate(focuses.items()):
        params[f"focus_ind_{n}"] = industry_id
        params[f"focus_re_{n}"] = f["pattern"]
        parts.append(f"({alias}.industry_id = %(focus_ind_{n})s and ({text}) ~* %(focus_re_{n})s)")
    return "(" + " or ".join(parts) + ")"
