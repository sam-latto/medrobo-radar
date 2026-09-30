"""Text clean-up for model-written prose shown in the dashboard and email."""
import re

_INDEX_NUMBERS = r"(?:index|indices|idx\.?)\s*[\d,/\s\-–—&and]*\d"
# A whole parenthetical: "(index 4)", "(indices 83–84)", "[indices 3, 5 and 9]", "(see index 12)"
_INDEX_REF_RE = re.compile(rf"\s*[\(\[](?:see\s+)?{_INDEX_NUMBERS}\s*[\)\]]", re.IGNORECASE)
# The tail of a larger parenthetical: "($200M, index 0)" -> "($200M)"
_INDEX_TAIL_RE = re.compile(rf"\s*[,;]\s*{_INDEX_NUMBERS}\s*(?=[\)\]])", re.IGNORECASE)
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")


def strip_index_refs(text: str) -> str:
    """Remove references to internal event indices that mean nothing to a reader."""
    return _INDEX_TAIL_RE.sub("", _INDEX_REF_RE.sub("", text or ""))


def md_safe(text: str) -> str:
    """Escape '$' so Streamlit markdown doesn't render '$500M … $1.4B' as LaTeX math."""
    return _UNESCAPED_DOLLAR_RE.sub(r"\\$", text or "")


def html_safe_dollars(text: str) -> str:
    """For HTML passed to st.markdown(unsafe_allow_html=True): '$' as an entity."""
    return (text or "").replace("$", "&#36;")
