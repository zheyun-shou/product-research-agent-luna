"""Tell a normal amazon.se page from a captcha, a sign-in redirect or an error."""
from scout.sources.amazon_se.reviews import has_review_list
from scout.sources.amazon_se.urls import page_kind
from scout.sources.base import RawPage, SessionStatus


def check_page(page: RawPage) -> SessionStatus:
    if "/ap/signin" in page.final_url:
        return SessionStatus(ok=False, problem="signin", detail="amazon.se asks you to sign in")
    if "/errors/validateCaptcha" in page.html:
        return SessionStatus(ok=False, problem="captcha", detail="amazon.se shows a captcha")
    if page.status in (404, 410):
        return SessionStatus(ok=False, problem="gone", detail=f"page not found ({page.status})")
    # No answer (for example the browser window was closed) or too many requests: the shopper must look
    if page.status in (0, 429):
        return SessionStatus(ok=False, problem="blocked", detail=f"page did not load ({page.status})")
    # A server error: on 2026-10-06 most 503 pages loaded on the next try
    if page.status >= 500:
        return SessionStatus(ok=False, problem="unavailable", detail=f"page did not load ({page.status})")
    # Seen once in 96 fetches: a 200 page with only the navigation skeleton.
    if page_kind(page.url) == "product" and 'id="productTitle"' not in page.html:
        return SessionStatus(ok=False, problem="incomplete", detail="the product page loaded without its content")
    if page_kind(page.url) == "reviews" and not has_review_list(page.html):
        return SessionStatus(ok=False, problem="incomplete", detail="the review page loaded without its review list")
    return SessionStatus(ok=True)
