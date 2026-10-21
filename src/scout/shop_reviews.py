"""Buyer reviews that a shop page loads apart from the page (plan 2026-10-20-review-sources.md): Prisjakt from its
review API, and Elgiganten from the Feefo widget. The other shops keep the reviews of their page data (JSON-LD).

Each review has the form of ProductRecord.reviews (title, rating, date, verified, text), never the author. The
market or the locale of a review is put in front of its title, because Prisjakt and Feefo mix the Nordic sites.

Each request of a review service goes to the fetch log, as the pages do (scout.shops.store_page)."""
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlparse

import httpx

from scout.fetch import HEADERS, SITE_GAP, site_of

MAX_REVIEWS = 50  # for one product page
TIMEOUT = 20.0
PRISJAKT_API = "https://www.prisjakt.nu/api/products/{id}/reviews"
FEEFO_API = "https://api.feefo.com/api/10/reviews/product"
FEEFO_MERCHANTS = {"elgiganten.se": "elgiganten-sweden"}

Post = Callable[..., httpx.Response]
Get = Callable[..., httpx.Response]


def prisjakt_id(url: str) -> str | None:
    found = parse_qs(urlparse(url).query).get("p")
    return found[0] if found and found[0].isdigit() else None


def prisjakt_reviews(url: str, post: Post = httpx.post) -> list[dict]:
    product = prisjakt_id(url)
    if product is None:
        return []
    response = post(PRISJAKT_API.format(id=product), json={"pageSize": MAX_REVIEWS},
                    headers={**HEADERS, "Accept": "application/json", "Referer": url}, timeout=TIMEOUT)
    if response.status_code != 200:
        return []
    found = []
    for r in (response.json().get("reviews") or [])[:MAX_REVIEWS]:
        text = " ".join(str(r.get("text") or "").split())
        if text:
            found.append({"title": f"Prisjakt {r.get('market') or ''}".strip(), "rating": r.get("rating"),
                          "date": str(r.get("createdAt") or "")[:10], "verified": False, "text": text})
    return found


def feefo_sku(url: str) -> str | None:
    """The article number: the last part of an Elgiganten product address."""
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return last if re.fullmatch(r"\d{3,}", last) else None


def feefo_reviews(url: str, get: Get = httpx.get) -> list[dict]:
    merchant, sku = FEEFO_MERCHANTS.get(site_of(url)), feefo_sku(url)
    if merchant is None or sku is None:
        return []
    response = get(FEEFO_API, params={"product_sku": sku, "merchant_identifier": merchant, "since_period": "ALL",
                                      "page_size": MAX_REVIEWS}, headers={**HEADERS, "Accept": "application/json"},
                   timeout=TIMEOUT)
    if response.status_code != 200:
        return []
    found = []
    for r in (response.json().get("reviews") or [])[:MAX_REVIEWS]:
        for p in r.get("products") or []:
            text = " ".join(str(p.get("review") or "").split())
            if text:
                rating = (p.get("rating") or {}).get("rating")
                found.append({"title": f"Feefo {r.get('locale') or ''}".strip(), "rating": rating,
                              "date": str(p.get("created_at") or r.get("last_updated_date") or "")[:10],
                              "verified": False, "text": text})
    return found


_last: dict[str, float] = {}
_gap_lock = threading.Lock()


def polite(site: str, gap: float = SITE_GAP, sleep=time.sleep, clock=time.monotonic) -> None:
    """The same gap between two requests of one site as for its pages (scout.fetch). The first request waits the
    full gap too, because the product page of the site loaded just before it."""
    with _gap_lock:
        last = _last.get(site)
        wait = gap if last is None else last + gap - clock()
        if wait > 0:
            sleep(wait)
        _last[site] = clock()


@dataclass
class ApiCall:
    """One request of a review service, for the fetch log."""
    source: str
    url: str
    status: str
    http_status: int | None
    duration_ms: int
    note: str = ""


def logged(send, source: str, log: list[ApiCall], clock=time.monotonic):
    """send, with each request added to log."""
    def run(url, **kwargs):
        address, start = str(httpx.URL(url, params=kwargs.get("params") or {})), clock()
        try:
            response = send(url, **kwargs)
        except Exception as exc:
            log.append(ApiCall(source, address, "failed", None, round((clock() - start) * 1000),
                               f"{type(exc).__name__}: {exc}"[:300]))
            raise
        log.append(ApiCall(source, address, "ok" if response.status_code == 200 else "failed", response.status_code,
                           round((clock() - start) * 1000)))
        return response
    return run


def more_reviews(url: str, get: Get = httpx.get, post: Post = httpx.post, wait=polite,
                 log: list[ApiCall] | None = None) -> list[dict]:
    """The reviews that the page loads apart from its page data, for the sites that have such a reader. log: gets
    the requests of the review services."""
    site, log = site_of(url), log if log is not None else []
    try:
        if site == "prisjakt.nu":
            wait(site)
            return prisjakt_reviews(url, logged(post, site, log))
        if site in FEEFO_MERCHANTS:
            wait("api.feefo.com")
            return feefo_reviews(url, logged(get, "api.feefo.com", log))
    except Exception as exc:  # an error or an unknown answer of the review service: the page data still counts
        if log and log[-1].status == "ok":
            log[-1].status, log[-1].note = "failed", f"unknown answer ({type(exc).__name__})"
        return []
    return []
