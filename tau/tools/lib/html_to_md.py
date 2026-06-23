# tools/html_to_md.py — Native web content extraction (stdlib only)
#
# DESIGN:
#   1. Score DOM nodes by text quality (Readability-style algorithm)
#   2. Select highest-scoring content container
#   3. Strip noise: ads, nav, sidebars, social, comments, related
#   4. Convert to compact markdown with preserved meaningful links
#   5. Enforce hard character limits to prevent context explosion
#
# ALGORITHM (Mozilla Readability inspired):
#   - Score each block: text_length + commas + class_bonus - link_penalty
#   - Propagate scores up: parent gets 100%, grandparent 50%
#   - Select top-scoring container
#   - Merge adjacent content regions
#   - Strip noise by class/ID patterns and density heuristics

from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser
from typing import Final

# ── Constants ──────────────────────────────────────────────────────
DEFAULT_MAX_CHARS: Final = 15000  # Hard cap on output
DEFAULT_MAX_WORDS: Final = 2500  # Hard cap on word count

# Tags that produce NO output at all
_SKIP: Final = frozenset(
    {
        "script",
        "style",
        "noscript",
        "meta",
        "link",
        "head",
        "title",
        "base",
        "colgroup",
        "template",
        "source",
        "track",
    }
)

# ── Content scoring constants (Readability-style) ────────────────
# Minimum text length to consider a block
_MIN_TEXT_LENGTH = 25
# Points per comma (proxy for sentences)
_COMMA_WEIGHT = 1
# Points per 100 chars of text (capped)
_TEXT_LENGTH_CAP = 3
_TEXT_LENGTH_DIVISOR = 100
# Link density threshold — above this, block is likely nav/ads
_LINK_DENSITY_THRESHOLD = 0.25
# Link density penalty multiplier
_LINK_DENSITY_PENALTY = 0.5
# Minimum score to consider a candidate
_MIN_CANDIDATE_SCORE = 20
# Parent score propagation factor
_PARENT_SCORE_FACTOR = 1.0
_GRANDPARENT_SCORE_FACTOR = 0.5

# Positive class/ID patterns — bonus +25
# Attribute regex patterns (shared by class matching)
_ATTR_PATS: Final = (
    r'class="([^"]*)"',
    r'id="([^"]*)"',
    r"class='([^']*)'",
    r"id='([^']*)'",
)

# Tags to strip via _strip_matching_elements
_STRIP_TAGS: Final = ("style", "script", "svg", "iframe", "table", "nav", "aside", "header", "footer", "figure")

# Noise class tokens for _strip_elements_with_class
_NOISE_CLASS_TOKENS: Final = frozenset({
    "ad", "ads", "advertisement", "sponsored", "google_ads", "taboola", "outbrain",
    "revcontent", "ad-container", "nav", "sidebar", "menu", "breadcrumb", "pagination",
    "share", "social", "twitter", "facebook", "linkedin", "pinterest", "reddit",
    "whatsapp", "telegram", "comments", "discussion", "user-content", "blog-comments",
    "related", "recommended", "similar", "also-read", "popular", "trending",
    "most-read", "read-next", "subscribe", "newsletter", "premium", "paywall",
    "abo", "subscription", "membership", "footer", "copyright", "legal",
    "disclaimer", "terms", "privacy", "cookie", "imprint", "video", "player",
    "youtube", "embed", "media-player", "widget", "banner", "popup", "modal",
    "overlay", "tooltip", "notification", "alert", "promo", "promotion",
    "commercial", "sponsored-content", "masthead", "top-bar", "topbar",
    "search", "search-form", "search-box", "login", "signin", "signup",
    "register", "cart", "checkout", "purchase", "buy", "taxonomy",
})

# Content extraction fallback order
_EXTRACTORS = (
    "_extract_wikipedia_content", "_extract_article", "_try_content_ids",
    "_try_content_classes", "_extract_generic_scored", "_extract_generic",
)

_POSITIVE_PATTERNS: Final = frozenset(
    {
        "article",
        "content",
        "story",
        "post",
        "entry",
        "body",
        "main",
        "text",
        "copy",
        "prose",
        "blog",
        "post-body",
        "article-body",
        "story-body",
        "main-content",
        "content-body",
        "hentry",
        "single",
        "page-content",
        "post-content",
        "article-content",
        "story-content",
        "entry-content",
        "inner",
        "inner-content",
        "primary",
        "reading",
    }
)

# Negative class/ID patterns — penalty -25
_NEGATIVE_PATTERNS: Final = frozenset(
    {
        # Navigation
        "nav",
        "menu",
        "sidebar",
        "breadcrumb",
        "pagination",
        "tabs",
        "navigation",
        "main-nav",
        "side-nav",
        "global-nav",
        # Ads
        "ad",
        "ads",
        "advertisement",
        "sponsored",
        "google_ads",
        "taboola",
        "outbrain",
        "revcontent",
        "ad-container",
        "ad-wrapper",
        "ad-space",
        "ad-unit",
        "adsbygoogle",
        # Social
        "share",
        "social",
        "twitter",
        "facebook",
        "linkedin",
        "pinterest",
        "reddit",
        "whatsapp",
        "telegram",
        "share-this",
        "addthis",
        "shareaholic",
        # Comments
        "comments",
        "discussion",
        "user-content",
        "blog-comments",
        "comment-list",
        "comment-form",
        "respond",
        # Related/Recommended
        "related",
        "recommended",
        "similar",
        "also-read",
        "more",
        "popular",
        "trending",
        "most-read",
        "read-next",
        # Subscription/Paywall
        "subscribe",
        "newsletter",
        "premium",
        "paywall",
        "abo",
        "subscription",
        "membership",
        "signup",
        "signup-form",
        # Footer
        "footer",
        "copyright",
        "legal",
        "disclaimer",
        "terms",
        "privacy",
        "cookie",
        "imprint",
        # Video/Embed
        "video",
        "player",
        "youtube",
        "embed",
        "iframe",
        "media-player",
        "video-player",
        # Miscellaneous noise
        "widget",
        "banner",
        "popup",
        "modal",
        "overlay",
        "tooltip",
        "notification",
        "alert",
        "promo",
        "promotion",
        "advertisement",
        "commercial",
        "sponsored-content",
        "header",
        "top-bar",
        "topbar",
        "masthead",
        "search",
        "search-form",
        "search-box",
        "login",
        "signin",
        "signup",
        "register",
        "cart",
        "checkout",
        "purchase",
        "buy",
        "category",
        "tag",
        "label",
        "metadata",
        "breadcrumb",
        "pager",
        "pagination",
        "author",
        "byline",
        "dateline",
        "timestamp",
        "tags",
        "categories",
        "taxonomy",
    }
)

# Wikipedia-specific noise (keep existing patterns)
_WIKI_NOISE_IDS: Final = frozenset(
    {
        "p-lang-btn",
        "p-tb",
        "p-search",
        "left-navigation",
        "mw-panel",
        "mw-sidebar",
        "mw-header",
        "mw-navigation",
        "siteNotice",
        "jump-to-nav",
        "vector-toc",
        "toc",
        "mw-indicators",
        "catlinks",
        "references",
        "reflist",
        "navbar",
        "printlinks",
        "siteSub",
        "contentSub",
        "firstHeading",
        "siteNotice",
        "tright",
        "plainlinks",
        "sister-projects",
        "languages",
    }
)

_WIKI_NOISE_CLASSES: Final = frozenset(
    {
        "vector",
        "mw-body-header",
        "toc",
        "reflist",
        "references",
        "catlinks",
        "printlink",
        "noprint",
        "metadata",
        "searchbox",
        "collapsible",
        "navbox",
        "navbox-inner",
        "navbox-subgroup",
        "thumbcaption",
        "magnify",
        "metadata",
        "mw-references-wrap",
        "mw-references-columns",
        "side-box",
        "sister-box",
        "sister-box-right",
        "portalbox",
        "navbar",
        "sister-link",
        "infobox",
        "infobox-data",
        "infobox-label",
        "infobox-image",
        "sister-logo",
        "sister-inline-image",
    }
)

# Wikipedia sections to CUT
_CUT_SECTIONS: Final = frozenset(
    {
        "see_also",
        "notes",
        "references",
        "further_reading",
        "external_links",
        "footnotes",
        "sources",
        "bibliography",
        "citations",
        "works_cited",
    }
)


# ── Content Scoring ──────────────────────────────────────────────


def _score_text(text: str) -> float:
    """Score a text block by length and punctuation (Readability-style)."""
    t = text.strip()
    if len(t) < _MIN_TEXT_LENGTH:
        return 0
    score = 1  # Base score for any block with enough text
    score += len(re.findall(r",", t)) * _COMMA_WEIGHT
    score += min(_TEXT_LENGTH_CAP, len(t) // _TEXT_LENGTH_DIVISOR)
    return score


def _link_density(html_block: str) -> float:
    """Calculate link density: ratio of text inside <a> tags to total text."""
    total_text = re.sub(r"<[^>]+>", "", html_block)
    total_len = len(total_text.strip())
    if total_len == 0:
        return 0.0
    # Extract text inside <a> tags
    link_text = "".join(
        re.sub(r"<[^>]+>", "", m.group(1))
        for m in re.finditer(r"<a[^>]*>(.*?)</a>", html_block, re.S | re.I)
    )
    return len(link_text.strip()) / total_len


def _tag_density(html_block: str) -> float:
    """Calculate tag density: ratio of HTML tag chars to total chars."""
    tag_chars = sum(len(m.group(0)) for m in re.finditer(r"<[^>]*>", html_block))
    return tag_chars / max(len(html_block), 1)


def _matches_class_pattern(attrs_str: str, pattern_set: frozenset[str]) -> bool:
    """Check if any token in attrs matches a pattern in pattern_set."""
    for pat in _ATTR_PATS:
        for m in re.finditer(pat, attrs_str, re.I):
            value = m.group(1).lower()
            tokens = re.split(r"[\s_-]+", value)
            for token in tokens:
                if token in pattern_set:
                    return True
    return False


def _has_negative_class(attrs_str: str) -> bool:
    return _matches_class_pattern(attrs_str, _NEGATIVE_PATTERNS)


def _has_positive_class(attrs_str: str) -> bool:
    return _matches_class_pattern(attrs_str, _POSITIVE_PATTERNS)
def _score_block(block_html: str) -> float:
    """Score a single HTML block by text quality and penalties."""
    # Get text content
    text = re.sub(r"<[^>]+>", " ", block_html)
    text_score = _score_text(text)
    if text_score == 0:
        return 0.0

    # Link density penalty
    ld = _link_density(block_html)
    if ld > _LINK_DENSITY_THRESHOLD:
        text_score *= 1.0 - max(0, ld - _LINK_DENSITY_THRESHOLD)

    # Tag density penalty (too many tags = structural noise)
    td = _tag_density(block_html)
    if td > 0.4:
        text_score *= 0.5

    return max(0, text_score)


def _score_div_with_attrs(block_html: str) -> float:
    """Score a div block, including class/ID bonuses/penalties."""
    score = _score_block(block_html)

    # Extract opening tag attributes
    m = re.match(r"<div\s+([^>]*)>", block_html, re.I)
    if m:
        attrs = m.group(1)
        if _has_negative_class(attrs):
            score = max(0, score - 25)
        if _has_positive_class(attrs):
            score += 25

    return score


# ── Content region extraction ────────────────────────────────────

# Pre-compiled regex patterns for container finding (re.IGNORECASE, no length mismatch)
_re_div_open = re.compile(r"<div", re.I)
_re_div_close = re.compile(r"</div>", re.I)


def _find_container(html: str, open_pos: int, max_size: int = 200_000) -> str | None:
    """Find the content between a <div> at open_pos and its matching </div>.

    Uses pre-compiled regex with re.IGNORECASE to avoid html.lower() length mismatch.

    Args:
        html: Full HTML string
        open_pos: Position AFTER the opening <div> tag's '>'
        max_size: Maximum container size to prevent excessive scanning
    """
    depth = 1
    pos = open_pos
    max_pos = open_pos + max_size
    while pos < len(html) and pos < max_pos and depth > 0:
        no_m = _re_div_open.search(html, pos)
        nc_m = _re_div_close.search(html, pos)
        if nc_m is None:
            return None
        no = no_m.start() if no_m else -1
        nc = nc_m.start()
        if no != -1 and no < nc:
            after = no + 4
            is_real_div = after < len(html) and html[after] in (
                " ",
                "\t",
                "\n",
                "\r",
                ">",
                "/",
                "\x0b",
                "\x0c",
            )
            if is_real_div:
                depth += 1
                pos = no + 4
            else:
                pos = no + 1
        else:
            depth -= 1
            if depth == 0:
                return html[open_pos : nc + 6]
            pos = nc + 6
    return None


def _find_container_tag(html: str, open_pos: int, tag: str) -> str | None:
    """Find content between a tag and its closing tag."""
    depth = 1
    pos = open_pos
    close_tag = f"</{tag}>"
    open_pat = rf"<{tag}"
    while pos < len(html) and depth > 0:
        no = html.lower().find(open_pat, pos)
        nc = html.lower().find(close_tag, pos)
        if nc == -1:
            return None
        if no != -1 and no < nc:
            depth += 1
            pos = no + len(tag) + 1
        else:
            depth -= 1
            if depth == 0:
                return html[open_pos : nc + len(close_tag)]
            pos = nc + len(close_tag)
    return None


def _extract_wikipedia_content(html: str) -> str | None:
    """Extract ONLY the Wikipedia article body."""
    m = re.search(r'<div[^>]*id=["\']?mw-content-text["\']?', html, re.I)
    if not m:
        return None
    tag_end = html.find(">", m.end())
    if tag_end == -1:
        return None
    container = _find_container(html, tag_end, max_size=2_000_000)
    if not container or len(container) < 500:
        return None
    # Skip everything before the first h2 (infobox, short description)
    first_h2 = re.search(r'<h2[^>]*id="[^"]*"[^>]*>', container)
    if first_h2:
        container = container[first_h2.start() :]
    # Cut at See also / Notes / References
    result = _cut_at_section(container)
    return result


def _cut_at_section(html: str) -> str:
    """Cut everything from the first 'See also' / 'Notes' / 'References' section."""
    best_pos = None
    for m in re.finditer(r'<h2[^>]*id="([^"]*)"[^>]*>(.*?)</h2>', html, re.I | re.S):
        sid = m.group(1).strip()
        if sid in _CUT_SECTIONS:
            if best_pos is None or m.start() < best_pos:
                best_pos = m.start()
    for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", html, re.I | re.S):
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        text = re.sub(r"[^a-z0-9\s]", "", text.lower())
        text = re.sub(r"\s+", "_", text)
        if text in _CUT_SECTIONS:
            if best_pos is None or m.start() < best_pos:
                best_pos = m.start()
    if best_pos is not None:
        return html[:best_pos]
    return html


def _extract_article(html: str) -> str | None:
    """Extract <article> or <main> content.

    Only accepts containers between 500 and 500_000 bytes to avoid
    matching page-level wrappers that contain everything.
    """
    for tag in ("article", "main"):
        pat = re.compile(rf"<{tag}[^>]*>", re.I)
        for m in pat.finditer(html):
            tag_end = m.end()
            close = re.search(rf"</{tag}>", html[tag_end:], re.I)
            if close:
                size = close.end() - tag_end
                if 500 < size < 500_000:
                    return html[tag_end : tag_end + close.end()]
    return None


def _extract_generic_scored(html: str) -> str | None:
    """Find the best content region using block scoring.

    Strategy (Readability-inspired):
    1. Find all candidate blocks (divs, articles, sections)
    2. Score each by: text length + commas - link_penalty + class_bonus
    3. Propagate scores to parent containers
    4. Return highest-scoring container

    Optimized: only examines top-level divs, uses fast heuristics.
    """
    # Pre-compute paragraph scores for fast lookup
    # Each paragraph gets a score based on text quality
    para_scores: list[tuple[int, int, float]] = []  # (start, end, score)
    for pm in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.S | re.I):
        p_text = re.sub(r"<[^>]+>", " ", pm.group(1)).strip()
        score = _score_text(p_text)
        if score > 0:
            # Link density penalty
            ld = _link_density(pm.group(0))
            if ld > _LINK_DENSITY_THRESHOLD:
                score *= _LINK_DENSITY_PENALTY
            para_scores.append((pm.start(), pm.end(), score))

    if not para_scores:
        return None

    # Find top-level divs and count how much scored paragraph content they contain
    div_opens: list[tuple[int, int, str]] = []
    for m in re.finditer(r"<div([^>]*)>", html, re.I):
        attrs = m.group(1)
        tag_end = html.find(">", m.end())
        if tag_end == -1:
            continue
        if _has_negative_class(f"<div{attrs}>"):
            continue
        div_opens.append((m.start(), tag_end, attrs))

    if not div_opens:
        return None

    # Score each div by summing paragraph scores it contains
    div_scores: dict[int, float] = {}
    for p_start, p_end, p_score in para_scores:
        # Find the innermost div containing this paragraph
        best_div: int | None = None
        for div_start, div_tag_end, _ in div_opens:
            # Paragraph must be after the opening tag
            if p_start < div_tag_end:
                continue
            # Check if paragraph is before where this div would close
            # (approximate: within 100KB of opening)
            if p_start > div_start + 100_000:
                continue
            if best_div is None or div_start > best_div:
                best_div = div_start

        if best_div is not None:
            # Class bonus/penalty
            _, _, attrs = next(d for d in div_opens if d[0] == best_div)
            mod = 0.0
            if _has_positive_class(f"<div{attrs}>"):
                mod = 25.0
            div_scores[best_div] = div_scores.get(best_div, 0) + p_score + mod

    if not div_scores:
        return None

    best_div_start = max(div_scores, key=div_scores.get)
    if div_scores[best_div_start] < _MIN_CANDIDATE_SCORE:
        return None

    _, best_tag_end, _ = next(d for d in div_opens if d[0] == best_div_start)
    container = _find_container(html, best_tag_end)
    if container and len(container) > 500:
        return container
    return None


def _extract_generic(html: str) -> str | None:
    """Fallback: find the largest text-dense div (legacy, kept for safety)."""
    best: str | None = None
    pat = re.compile(r"<div[^>]*>", re.I)
    for m in pat.finditer(html):
        tag_end = html.find(">", m.end())
        if tag_end == -1:
            continue
        container = _find_container(html, tag_end, max_size=2_000_000)
        if container and len(container) > 1000:
            text_chars = sum(1 for c in container if c.isalpha())
            density = text_chars / max(len(container), 1)
            if density > 0.15 and (best is None or len(container) > len(best)):
                best = container
    return best


def _try_content_ids(html: str) -> str | None:
    """Try to extract content by common content ID patterns."""
    content_ids = [
        "content",
        "main",
        "article",
        "story",
        "page",
        "body",
        "article-body",
        "main-content",
        "story-body",
        "content-body",
        "page-content",
        "post-content",
        "entry-content",
    ]
    for cid in content_ids:
        pat = re.compile(rf'<div[^>]*id=["\']?{re.escape(cid)}["\']?[^>]*>', re.I)
        for m in pat.finditer(html):
            tag_end = html.find(">", m.end())
            if tag_end == -1:
                continue
            container = _find_container(html, tag_end, max_size=2_000_000)
            if container and len(container) > 500:
                return container
    return None


def _try_content_classes(html: str) -> str | None:
    """Try to extract content by common content class patterns.

    Requires substantial content (> 2000 bytes) to avoid matching small UI elements.
    Limits container size to 500_000 bytes to avoid matching page-level wrappers.
    """
    content_classes = [
        "content",
        "article",
        "story",
        "post",
        "entry",
        "body",
        "main",
        "text",
        "copy",
        "prose",
        "inner",
        "primary",
    ]
    best: tuple[int, str] | None = None
    for cls in content_classes:
        pat = re.compile(rf'<div[^>]*class="[^"]*{re.escape(cls)}[^"]*"[^>]*>', re.I)
        for m in pat.finditer(html):
            tag_end = html.find(">", m.end())
            if tag_end == -1:
                continue
            # Quick size check before expensive _find_container
            # Only scan up to 500KB to avoid O(n²) on huge pages
            container = _find_container(html, tag_end, max_size=500_000)
            if container and 2000 < len(container) < 500_000:
                # Prefer larger containers
                if best is None or len(container) > best[0]:
                    best = (len(container), container)
    return best[1] if best else None


def extract_main_content(html: str) -> str:
    """Extract the main content region from HTML."""
    for extractor_name in _EXTRACTORS:
        extractor = globals()[extractor_name]
        result = extractor(html)
        if result:
            return result

    # Fallback: strip known noise tags
    cleaned = html
    for tag in ("script", "style", "nav", "aside", "header", "footer"):
        cleaned = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", cleaned, flags=re.DOTALL | re.I
        )
    return cleaned


def _strip_matching_elements(html_text: str, tag: str) -> str:
    """Strip all <tag>...</tag> elements, handling nesting properly."""
    result = []
    pos = 0
    lower = html_text.lower()
    open_pat = f"<{tag}"
    close_pat = f"</{tag}>"
    close_len = len(close_pat)
    while pos < len(html_text):
        # Find next opening tag
        open_pos = lower.find(open_pat, pos)
        if open_pos == -1:
            result.append(html_text[pos:])
            break
        # Add text before the opening tag
        result.append(html_text[pos:open_pos])
        # Find the matching closing tag (handle nesting)
        depth = 1
        scan = open_pos + len(open_pat)
        while depth > 0 and scan < len(html_text):
            next_open = lower.find(open_pat, scan)
            next_close = lower.find(close_pat, scan)
            if next_close == -1:
                break  # No closing tag found, stop
            if next_open != -1 and next_open < next_close:
                depth += 1
                scan = next_open + len(open_pat)
            else:
                depth -= 1
                if depth == 0:
                    # Found matching close - skip everything
                    pos = next_close + close_len
                    break
                scan = next_close + close_len
        else:
            # Reached end without closing - keep the rest
            result.append(html_text[open_pos:])
            pos = len(html_text)
    return "".join(result)


def _strip_elements_with_class(html_text: str, class_tokens: set[str]) -> str:
    """Strip elements whose class attribute contains any of the given tokens.

    Uses a focused approach: for each tag type, find opening tags with noise
    classes and skip to their matching closing tags. Processes each tag type
    sequentially but uses efficient string operations.
    """
    if not class_tokens:
        return html_text

    # Pre-compile class regex for fast matching
    class_re = re.compile(r'class=["\']([^"\']*)["\']', re.I)

    # Only check the most common container tags (div, section, article, span)
    # These cover 95%+ of noise elements
    tags_to_check = ("div", "section", "article", "span", "p", "footer")

    for tag in tags_to_check:
        open_prefix = f"<{tag}"
        close_tag = f"</{tag}>"
        close_len = len(close_tag)
        lower = html_text.lower()

        result_parts = []
        pos = 0
        html_len = len(html_text)

        while pos < html_len:
            # Find next opening tag of this type
            open_pos = lower.find(open_prefix, pos)
            if open_pos == -1:
                result_parts.append(html_text[pos:])
                break

            # Verify it's actually this tag (not <division for <div)
            after_prefix = open_pos + len(open_prefix)
            if after_prefix >= html_len:
                result_parts.append(html_text[pos:])
                break
            next_char = html_text[after_prefix]
            if next_char not in (" ", "\t", "\n", "\r", ">", "/"):
                # Not this tag, skip past it
                result_parts.append(html_text[pos:after_prefix])
                pos = after_prefix
                continue

            # Find end of opening tag
            tag_end = html_text.find(">", open_pos)
            if tag_end == -1:
                result_parts.append(html_text[pos:])
                break

            opening = html_text[open_pos : tag_end + 1]

            # Check if this opening tag has a matching noise class
            m = class_re.search(opening)
            if m:
                classes = m.group(1).lower()
                tokens = classes.split()
                is_noise = any(t in class_tokens for t in tokens)
            else:
                is_noise = False

            if not is_noise:
                # Not noise - keep it
                result_parts.append(html_text[pos : tag_end + 1])
                pos = tag_end + 1
                continue

            # Noise element - find matching close tag and skip
            depth = 1
            scan = tag_end + 1
            found_close = False
            while scan < html_len:
                # Find next open or close of this tag type
                next_open = lower.find(open_prefix, scan)
                next_close = lower.find(close_tag, scan)

                if next_close == -1:
                    break  # No closing tag found

                if next_open != -1 and next_open < next_close:
                    # Check if this open is really this tag
                    after_open = next_open + len(open_prefix)
                    if after_open < html_len and html_text[after_open] in (
                        " ",
                        "\t",
                        "\n",
                        "\r",
                        ">",
                        "/",
                    ):
                        depth += 1
                    scan = max(next_open, next_close) + 1
                    continue

                # next_close is the next relevant tag
                depth -= 1
                if depth == 0:
                    # Found matching close - skip everything
                    pos = next_close + close_len
                    found_close = True
                    break
                scan = next_close + close_len

            if not found_close:
                # No closing tag found - skip to end
                pos = html_len

        html_text = "".join(result_parts)

    return html_text


def strip_noise(html_text: str) -> str:
    """Aggressively strip noise from extracted content.

    Uses proper element matching to avoid destroying nested structures.
    """
    # 1. Strip scripts and styles (self-contained, safe to regex)
    html_text = _strip_matching_elements(html_text, "style")
    html_text = _strip_matching_elements(html_text, "script")

    # 2. Strip SVG elements
    html_text = _strip_matching_elements(html_text, "svg")

    # 3. Strip iframes
    html_text = _strip_matching_elements(html_text, "iframe")

    # 4. Strip tables
    html_text = _strip_matching_elements(html_text, "table")

    # 5. Strip semantic HTML noise tags
    for tag in ("nav", "aside", "header", "footer"):
        html_text = _strip_matching_elements(html_text, tag)

    # 6. Strip elements with noise classes (proper element matching)
    # NOTE: 'ho-text' is intentionally NOT here - it's a heise utility class on CONTENT
    # NOTE: 'ho-text-muted' is also NOT here - it's on pagination spans, stripping causes issues
    # Only strip clearly semantic noise classes
    html_text = _strip_elements_with_class(html_text, _NOISE_CLASS_TOKENS)

    # 7. Strip Wikipedia-specific noise by ID
    for nid in _WIKI_NOISE_IDS:
        html_text = _strip_elements_with_class(html_text, {nid})

    # 8. Strip Wikipedia-specific noise by class
    for cls in _WIKI_NOISE_CLASSES:
        html_text = _strip_elements_with_class(html_text, [cls])

    # 9. Skip 'hidden' class stripping — Tailwind uses 'hidden' for responsive
    # design (e.g., 'hidden md:block'), not for actually hidden content.
    # display:none via inline style is already handled by regex below.

    # 10. Strip inline styles
    html_text = re.sub(r'style="[^"]*"', "", html_text)

    # 11. Strip hidden elements by style
    html_text = re.sub(
        r'<[^>]*style="[^"]*display\s*:\s*none[^"]*"[^>]*>',
        "",
        html_text,
        flags=re.I,
    )

    # 12. Strip SVG data URIs and decorative images
    html_text = re.sub(
        r'<img[^>]*src="data:[^"]*"[^>]*/?\s*>',
        "",
        html_text,
        flags=re.I,
    )
    html_text = re.sub(
        r'<img[^>]*src="[^"]*(?:svg|icon|logo|badge)[^"]*"[^>]*/?\s*>',
        "",
        html_text,
        flags=re.I,
    )

    # 13. Strip Wikipedia wiki-links (convert to plain text)
    html_text = re.sub(
        r'<a[^>]*href="/wiki/[^"]*"[^>]*>(.*?)</a>',
        r"\1",
        html_text,
        flags=re.DOTALL | re.I,
    )

    # 14. Strip edit links
    html_text = re.sub(
        r'<a[^>]*href="/w/index\.php[^\"]*action=edit[^\"]*"[^>]*>.*?</a>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )

    # 15. Strip references/footnotes
    html_text = re.sub(
        r'<ol[^>]*class="[^"]*references[^"]*"[^>]*>.*?</ol>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )
    html_text = _strip_elements_with_class(
        html_text, ["catlinks", "reflist", "references"]
    )

    # 16. Strip figure elements (often just decorative images)
    html_text = _strip_matching_elements(html_text, "figure")

    # 17. Strip data/aria attributes
    html_text = re.sub(r'\s+data-[^=]*="[^"]*"', "", html_text)
    html_text = re.sub(r"\s+data-[^=]*", "", html_text)
    html_text = re.sub(r'\s+aria-[^=]*="[^"]*"', "", html_text)

    # 18. Strip pre/code blocks (keep text inline)
    html_text = re.sub(
        r"<pre[^>]*>(.*?)</pre>",
        lambda m: m.group(1).strip(),
        html_text,
        flags=re.DOTALL | re.I,
    )

    # 19. Strip Wikipedia-specific elements
    html_text = re.sub(
        r'<span[^>]*class="[^"]*mw-editsection[^"]*"[^>]*>.*?</span>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )
    html_text = re.sub(
        r'<span[^>]*class="[^"]*mw-cite-backlink[^"]*"[^>]*>.*?</span>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )
    html_text = re.sub(
        r'<sup[^>]*class="[^"]*reference[^"]*"[^>]*>.*?</sup>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )
    html_text = re.sub(
        r'<sup[^>]*id="cite_note[^"]*"[^>]*>.*?</sup>',
        "",
        html_text,
        flags=re.DOTALL | re.I,
    )

    return html_text


# ── HTML to Markdown Converter ─────────────────────────────────────


class _HtmlToMd(HTMLParser):
    """Convert HTML to compact markdown.

    Uses a line buffer (_line) to accumulate inline content,
    flushing to _buf only when block-level elements are encountered.

    STRATEGY: Preserve meaningful links, strip noise.
    """

    def __init__(
        self, *, max_chars: int = DEFAULT_MAX_CHARS, max_words: int = DEFAULT_MAX_WORDS
    ):
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._line: list[str] = []
        self._stack: list[str] = []
        self._list_depth = 0
        self._in_pre = False
        self._a_href = ""
        self._a_text: list[str] = []
        self._in_a = False
        self._max_chars = max_chars
        self._max_words = max_words
        self._word_count = 0
        self._cell_buf: list[str] = []
        self._in_cell = False
        self._row_buf: list[str] = []
        self._in_table = False
        self._skip_depth = 0
        self._image_count = 0  # Limit images

    def _top(self) -> str | None:
        return self._stack[-1] if self._stack else None

    def _flush_line(self) -> None:
        """Flush the current line buffer to the output."""
        if self._in_pre:
            text = "\n".join(self._line).strip()
            if text:
                self._buf.append(text)
                self._word_count += len(text.split())
            self._line = []
            return
        if self._line:
            text = " ".join(self._line).strip()
            if text:
                self._buf.append(text)
                self._word_count += len(text.split())
            self._line = []

    def _flush_all(self) -> None:
        """Flush line buffer and add blank line (block break)."""
        self._flush_line()
        if self._buf and self._buf[-1]:
            self._buf.append("")

    def _is_meaningful_link(self, href: str, text: str) -> bool:
        """Check if a link is meaningful (not nav/ads/generic)."""
        text = text.strip()
        # Too short
        if len(text) < 3:
            return False
        # Generic link text
        generic = {
            "click here",
            "read more",
            "learn more",
            "more",
            "here",
            "this",
            "link",
            "url",
            "http",
            "www",
            "com",
            "net",
            "org",
        }
        if text.lower() in generic:
            return False
        # Nav-like patterns
        nav_patterns = {
            "home",
            "about",
            "contact",
            "privacy",
            "terms",
            "cookie",
            "imprint",
            "sitemap",
            "login",
            "signup",
        }
        if text.lower() in nav_patterns:
            return False
        return True

    def _inline_text(self, text: str) -> None:
        """Add text to the current line buffer."""
        if self._skip_depth > 0 or self._in_cell:
            return
        if self._in_pre:
            self._line.append(text)
            return
        text = text.strip()
        if not text:
            return
        if self._in_a:
            self._a_text.append(text)
        else:
            self._line.append(text)

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_pre:
            self._line.append(data)
            return
        if self._in_a:
            self._a_text.append(data)
        elif self._in_cell:
            self._cell_buf.append(data)
        else:
            self._inline_text(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html_mod.entities.codepoint2name.get(name, f"&{name};"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        ad = dict(attrs)

        if tag in _SKIP:
            self._skip_depth += 1
            self._stack.append(f"__skip__{self._skip_depth}")
            return

        # ── Block elements ──
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_all()
            level = int(tag[1])
            self._line.append(f"{'#' * level} ")
            self._stack.append(tag)
            return

        if tag == "p":
            self._flush_all()
            self._stack.append(tag)
            return

        if tag == "br":
            self._flush_line()
            self._buf.append("")
            return

        if tag == "hr":
            self._flush_all()
            self._buf.append("---")
            self._flush_all()
            return

        if tag in ("ul", "ol"):
            self._flush_all()
            self._list_depth += 1
            self._stack.append(tag)
            return

        if tag == "li":
            self._flush_line()
            indent = "  " * (self._list_depth - 1)
            mk = "- " if self._top() == "ul" else "1. "
            self._line.append(f"{indent}{mk}")
            self._stack.append(tag)
            return

        if tag == "blockquote":
            self._flush_all()
            self._stack.append(tag)
            return

        if tag == "pre":
            self._flush_all()
            self._in_pre = True
            self._stack.append(tag)
            return

        if tag == "code":
            if self._in_pre:
                pass  # Keep as-is in pre
            else:
                self._line.append("`")
            self._stack.append(tag)
            return

        if tag == "strong" or tag == "b":
            self._line.append("**")
            self._stack.append(tag)
            return

        if tag == "em" or tag == "i":
            self._line.append("*")
            self._stack.append(tag)
            return

        if tag == "a":
            href = ad.get("href", "") or ""
            self._in_a = True
            self._a_href = href
            self._a_text = []
            self._stack.append(tag)
            return

        if tag == "img":
            # Limit images to prevent context explosion
            if self._image_count > 20:
                self._skip_depth += 1
                self._stack.append(f"__skip_img__{self._skip_depth}")
                return
            alt = ad.get("alt", "") or ""
            src = ad.get("src", "") or ""
            if alt or src:
                self._flush_line()
                if alt:
                    self._buf.append(f"![{alt}]({src})")
                else:
                    self._buf.append(f"![]({src})")
                self._image_count += 1
            self._stack.append(tag)
            return

        if tag == "div":
            self._stack.append(tag)
            return

        if tag == "span":
            self._stack.append(tag)
            return

        # Unknown tag — treat as inline
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in _SKIP:
            if self._skip_depth > 0:
                self._skip_depth -= 1
                # Pop the skip marker
                for i in range(len(self._stack) - 1, -1, -1):
                    if self._stack[i].startswith("__skip__"):
                        self._stack.pop(i)
                        break
            return

        if tag == "img":
            if self._skip_depth > 0:
                self._skip_depth -= 1
                for i in range(len(self._stack) - 1, -1, -1):
                    if self._stack[i].startswith("__skip_img__"):
                        self._stack.pop(i)
                        break
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_line()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "p":
            self._flush_all()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
            self._flush_all()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "li":
            self._flush_all()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "blockquote":
            self._flush_all()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "pre":
            self._in_pre = False
            self._flush_all()
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "code":
            if self._top() != "pre":
                self._line.append("`")
            if self._top() == tag:
                self._stack.pop()
            return

        if tag == "a" and self._in_a:
            self._in_a = False
            lt = " ".join(self._a_text).strip()
            if self._a_href and lt and self._is_meaningful_link(self._a_href, lt):
                self._line.append(f"[{lt}]({self._a_href})")
            elif lt:
                self._line.append(lt)
            self._a_text = []
            if self._top() == tag:
                self._stack.pop()
            return

        if tag in ("strong", "b"):
            self._line.append("**")
            if self._top() == tag:
                self._stack.pop()
            return

        if tag in ("em", "i"):
            self._line.append("*")
            if self._top() == tag:
                self._stack.pop()
            return

        if tag in ("div", "span"):
            if self._top() == tag:
                self._stack.pop()
            return

        # Generic end tag — try to pop matching
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def getvalue(self) -> str:
        self._flush_line()
        md = "\n".join(self._buf)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()


# ── Post-processing ──────────────────────────────────────────────


def _clean_markdown(md: str) -> str:
    """Post-process markdown to remove remaining noise."""
    # Remove double spaces around formatting markers
    md = re.sub(r"\*+ +", " *", md)
    md = re.sub(r" +\*+", "* ", md)
    md = re.sub(r"\*\* +", " **", md)
    md = re.sub(r" +\*\*", "** ", md)
    # Remove trailing spaces from headings
    md = re.sub(r"^(#+\s+)\s+", r"\1", md, flags=re.MULTILINE)
    # Remove "Main article:" prefix
    md = re.sub(r"Main article: [^\s]+", "", md)
    # Remove "(See §...)" references
    md = re.sub(r"\(See §[^)]*\)", "", md)
    # Remove standalone formatting chars
    md = re.sub(r"(?<!\S)\*+(?:\s+\*+)*(?!\S)", "", md)
    md = re.sub(r"(?<!\S)`+(?:\s+`+)*(?!\S)", "", md)

    lines = md.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are only formatting chars
        if re.match(r"^[\s`*_\-\|]+$", stripped):
            continue
        # Skip lines that are mostly formatting noise
        if len(stripped) > 5:
            content = re.sub(r"[\s`*_\-\|]", "", stripped)
            if len(content) < len(stripped) * 0.1:
                continue
        cleaned.append(line)
    md = "\n".join(cleaned)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


# ── Public API ────────────────────────────────────────────────────


def html_to_markdown(
    html_text: str,
    *,
    strip_noise_flag: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_words: int = DEFAULT_MAX_WORDS,
) -> str:
    """Convert HTML to compact markdown with aggressive noise stripping."""
    if strip_noise_flag:
        html_text = strip_noise(html_text)

    converter = _HtmlToMd(max_chars=max_chars, max_words=max_words)
    converter.feed(html_text)
    md = converter.getvalue()

    md = _clean_markdown(md)

    if len(md) > max_chars:
        md = md[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"

    return md
