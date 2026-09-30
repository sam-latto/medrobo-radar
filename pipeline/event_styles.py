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
