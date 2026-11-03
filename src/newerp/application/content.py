"""Rich text the storefront shows as HTML (descriptions, size tables, pages): cleaned before saving.

nh3 (a binding of the Rust library ammonia) keeps only the listed tags and attributes, like HtmlSanitizer in .NET:
a <script>, an onclick=, a javascript: link or a style that hides content never reach the database, so an editor's
account cannot be used to attack the shop's customers (stored XSS)."""

import nh3

TAGS = {
    "a", "b", "blockquote", "br", "caption", "div", "em", "h2", "h3", "h4", "h5", "hr", "i", "img", "li", "ol", "p",
    "s", "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}  # fmt: skip
ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"class"},
}
URL_SCHEMES = {"http", "https", "mailto", "tel"}


def clean_html(value: str | None) -> str:
    """Safe HTML from what an editor typed; links get rel="noopener noreferrer"."""
    if not value or not value.strip():
        return ""
    return nh3.clean(value, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES, link_rel="noopener noreferrer")
