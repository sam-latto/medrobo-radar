"""Text clean-up for model-written prose shown in the dashboard and email."""
import html
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


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
_MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_MD_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_MD_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_STYLES = {
    1: "font-size:24px;line-height:1.25;margin:20px 0 8px;",
    2: "font-size:18px;margin:24px 0 8px;",
    3: "font-size:16px;margin:20px 0 6px;",
}


def _md_inline(text: str) -> str:
    text = _MD_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}" style="color:#1e5b72;">{m.group(1)}</a>', text)
    text = _MD_BOLD_RE.sub(r"<strong>\1</strong>", text)
    return _MD_ITALIC_RE.sub(r"<em>\1</em>", text)


def markdown_to_email_html(text: str) -> str:
    """Render the small Markdown subset the briefing uses (headings, lists, bold, italics, links)
    as email-safe HTML. Input is HTML-escaped first, so model text can't inject markup."""
    out: list[str] = []
    paragraph: list[str] = []
    list_tag: str | None = None

    def close_paragraph():
        if paragraph:
            out.append(f'<p style="margin:0 0 12px;">{_md_inline(" ".join(paragraph))}</p>')
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in html.escape(text or "", quote=False).splitlines():
        line = raw.strip()
        heading = _MD_HEADING_RE.match(line)
        bullet = _MD_BULLET_RE.match(line)
        numbered = _MD_NUMBERED_RE.match(line)
        if not line:
            close_paragraph()
            close_list()
        elif heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1))
            out.append(f'<h{level + 1} style="{_HEADING_STYLES[level]}">{_md_inline(heading.group(2))}</h{level + 1}>')
        elif bullet or numbered:
            close_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f'<{tag} style="margin:0 0 12px;padding-left:22px;">')
                list_tag = tag
            out.append(f'<li style="margin:0 0 6px;">{_md_inline((bullet or numbered).group(1))}</li>')
        else:
            close_list()
            paragraph.append(line)
    close_paragraph()
    close_list()
    return "\n".join(out)
