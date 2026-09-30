"""Badge colors and icons for event types, shared by the dashboard and email.

Profiles can define any event types, so unknown types get a stable color from a palette.
"""
import zlib

EVENT_TYPE_COLORS = {
    "funding": "#22c55e",
    "regulatory": "#3b82f6",
    "launch": "#f59e0b",
    "research": "#8b5cf6",
    "partnership_ma": "#ec4899",
    "deployment": "#14b8a6",
    "earnings": "#0ea5e9",
    "news": "#6b7280",
}

EVENT_TYPE_ICONS = {
    "funding": "💰",
    "regulatory": "🏛️",
    "launch": "🚀",
    "research": "🔬",
    "partnership_ma": "🤝",
    "deployment": "🏭",
    "earnings": "📈",
    "news": "📰",
}

_PALETTE = ["#0891b2", "#65a30d", "#d97706", "#7c3aed", "#db2777", "#0d9488", "#4f46e5"]


def event_color(event_type: str) -> str:
    if event_type in EVENT_TYPE_COLORS:
        return EVENT_TYPE_COLORS[event_type]
    return _PALETTE[zlib.crc32(event_type.encode()) % len(_PALETTE)]


def event_icon(event_type: str) -> str:
    return EVENT_TYPE_ICONS.get(event_type, "📌")


def event_label(event_type: str) -> str:
    return "PARTNERSHIP / M&A" if event_type == "partnership_ma" else event_type.replace("_", " ").upper()


SOURCE_LABELS = {
    "tavily": "Web",
    "openfda": "FDA",
    "sec_form_d": "SEC Form D",
    "pubmed": "PubMed",
    "arxiv": "arXiv",
    "clinicaltrials": "ClinicalTrials.gov",
}
PRIMARY_SOURCE_TYPES = {"openfda", "sec_form_d", "pubmed", "arxiv", "clinicaltrials"}


def source_label(source_type: str | None) -> str:
    return SOURCE_LABELS.get(source_type or "tavily", source_type or "Web")


def source_badge_html(source_type: str | None) -> str:
    primary = (source_type or "tavily") in PRIMARY_SOURCE_TYPES
    bg, fg = ("#0f172a", "#ffffff") if primary else ("#e2e8f0", "#334155")
    title = "Primary source" if primary else "Secondary source (web/news)"
    return (
        f'<span title="{title}" style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;margin-left:8px;">{source_label(source_type)}</span>'
    )
