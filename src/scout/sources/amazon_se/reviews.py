"""Read the review pages of amazon.se. The name and profile of the reviewer are never read."""
import hashlib
import re

from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_rating

# The star text that some pages put in front of the title, in English and Swedish
_STARS = re.compile(r"^\s*\d[.,]\d\s+(out of 5 stars|av 5 stjärnor)\s*", re.IGNORECASE)
_MARKS = dict.fromkeys(map(ord, "‎‏"), None)


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).translate(_MARKS).split())


def _original(el: Tag | None) -> str:
    """The words of the reviewer, without the hidden translation that amazon.se adds."""
    if el is None:
        return ""
    original = el.select_one(".cr-original-review-content")
    if original is not None:
        return _text(original)
    for hidden in el.select(".cr-translated-review-content, .aok-hidden"):
        hidden.decompose()
    return _text(el)


def parse_review_page(html: str) -> tuple[list[dict], int | None]:
    """The reviews of one review page, and the number of reviews that match the filter. Each review has
    review_id, title, rating, date, verified, variant and text."""
    soup = BeautifulSoup(html, "lxml")
    reviews = []
    for el in soup.select('[data-hook="review"]'):
        text = _original(el.select_one('[data-hook="review-body"]'))
        if not text:
            continue
        title = _STARS.sub("", _original(el.select_one('[data-hook="review-title"]')))
        stars = _text(el.select_one('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"]'))
        rating = parse_rating(stars) if stars else None
        review_id = el.get("id") or "h" + hashlib.sha1(f"{title}\n{text}".encode()).hexdigest()[:13]
        reviews.append({
            "review_id": review_id,
            "title": title,
            "rating": str(rating) if rating is not None else None,
            "date": _text(el.select_one('[data-hook="review-date"]')),
            "verified": el.select_one('[data-hook="avp-badge"]') is not None,
            "variant": _text(el.select_one('[data-hook="format-strip"], [data-hook="format-strip-linkless"]')),
            "text": text,
        })
    count_el = soup.select_one('[data-hook="cr-filter-info-review-rating-count"]')
    return reviews, parse_count(_text(count_el)) if count_el else None


def has_review_list(html: str) -> bool:
    """A review page that loaded has the review list, even when no review matches the filter."""
    return 'id="cm_cr-review_list"' in html
