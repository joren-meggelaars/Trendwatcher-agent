"""Turns feed text into plain text.

RSS/Atom summaries and even titles arrive as HTML ("<p>…</p>", links) and are
often entity-encoded twice ("&amp;quot;", "Merger &amp; Acquisition"), plus
WordPress' "The post X appeared first on Y." footer. Stored as-is and then
escaped again for the digest mail, that shows up as literal markup. This
cleans it once, at the source: when an item is stored and whenever titles or
summaries leave the service, so items stored before this existed come out
clean too.
"""

import html
import re

# Only real tags (</p>, <a href=...>), so prose like "a < b" survives.
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# WordPress footer: "The post <title> appeared first on <site>."
_WP_FOOTER_RE = re.compile(r"\s*The post\b.{0,300}?\bappeared first on\b.*$", re.DOTALL)


def clean_text(text: str | None) -> str:
    """Plain text: tags stripped, entities decoded (also when encoded twice),
    WordPress footer removed, whitespace collapsed."""
    if not text:
        return ""
    # Decoding can reveal tags ("&lt;p&gt;") and stripping can leave entities,
    # so repeat until stable (a few passes at most).
    for _ in range(3):
        cleaned = " ".join(_TAG_RE.sub(" ", html.unescape(text)).split())
        if cleaned == text:
            break
        text = cleaned
    return _WP_FOOTER_RE.sub("", text).strip()
