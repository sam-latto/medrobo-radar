from pipeline.text_utils import html_safe_dollars, md_safe, strip_index_refs


def test_md_safe_escapes_dollars_once():
    text = "RealSense for about $500 million, then a **$1.4B Series C**"
    assert md_safe(text) == r"RealSense for about \$500 million, then a **\$1.4B Series C**"
    assert md_safe(md_safe(text)) == md_safe(text)  # already-escaped dollars are left alone
    assert md_safe(None) == ""


def test_html_safe_dollars():
    assert html_safe_dollars("<strong> · $700,000,000</strong>") == "<strong> · &#36;700,000,000</strong>"


def test_strip_index_refs():
    text = ("Cognex bought RealSense (indices 83–84), and Neura raised $1.4B (index 4). "
            "Deals closed [indices 3, 5 and 9]; see also (see index 12). Keep (Series C) and (2026).")
    assert strip_index_refs(text) == (
        "Cognex bought RealSense, and Neura raised $1.4B. "
        "Deals closed; see also. Keep (Series C) and (2026)."
    )
    assert strip_index_refs("Cornerstone ($200M, index 0) and revenue (index 59/99).") == (
        "Cornerstone ($200M) and revenue."
    )
