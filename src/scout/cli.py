"""Command line for product-scout."""
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import typer
from pydantic import BaseModel

from scout.config import ROOT, load_settings
from scout.crawl import crawl as run_crawl
from scout.crawl import fetch_checked
from scout.db.conn import connect, init_schema
from scout.db.store import save_record
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.eval.prices import evaluate_prices, extract_from_snapshot, read_labels, write_template
from scout.llm.client import LLM, LLMOutputError, LLMUnavailable
from scout.llm.config import load_llm_config
from scout.picks.rank import apply_conditions, drop_implausible_sizes, load_cards, parse_condition, rank
from scout.picks.recheck import recheck_picks
from scout.picks.write import format_picks, write_picks
from scout.profiles import load_profile
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.search import has_next_page, parse_search_page
from scout.sources.amazon_se.urls import BASE_URL, SOURCE, page_kind, product_url, snapshot_key
from scout.sources.base import SessionStatus
from scout.sources.browser import BrowserSession
from scout.sources.snapshots import save_html
from scout.trace import tracer_from_env

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """product-scout command line."""
    # The Windows console defaults to GBK here and cannot print Swedish letters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo("product-scout 0.1.0")


@app.command("db-init")
def db_init() -> None:
    """Create the tables in the main database."""
    with connect(load_settings().database_url) as conn:
        init_schema(conn)
    typer.echo("Tables are ready.")


@app.command()
def browser(url: str = typer.Argument(BASE_URL + "/")) -> None:
    """Open the product-scout Chrome profile to set cookies, the delivery address or log in."""
    settings = load_settings()
    with BrowserSession(settings.browser_profile_dir) as session:
        session.show(url)
        typer.prompt("Press Enter here when you are done in the browser", default="", show_default=False)


@app.command()
def snap(urls: List[str], screenshot: bool = True) -> None:
    """Save snapshots of amazon.se pages, to check the selectors against real pages."""
    settings = load_settings()
    with BrowserSession(settings.browser_profile_dir) as session:
        for url in urls:
            page = session.fetch(url)
            status = check_page(page)
            path = save_html(page, settings.snapshot_dir, SOURCE, page_kind(url), snapshot_key(url))
            if screenshot:
                session.screenshot(path.with_suffix(".png"))
            typer.echo(f"{status.problem or 'ok':8} {path}")


@app.command("parse-search")
def parse_search_cmd(path: Path) -> None:
    """Print what the parser reads from a saved search page."""
    html = path.read_text(encoding="utf-8")
    for c in parse_search_page(html):
        typer.echo(json.dumps(c.model_dump(mode="json"), ensure_ascii=False))
    typer.echo(f"next page: {has_next_page(html)}")


@app.command("parse-product")
def parse_product_cmd(path: Path) -> None:
    """Print what the parser reads from a saved product page. The folder name is the ASIN."""
    fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    rec = parse_product_page(path.read_text(encoding="utf-8"), product_url(path.parent.name), fetched_at)
    shown = rec.model_dump(mode="json", exclude={"text"})
    typer.echo(json.dumps(shown, ensure_ascii=False, indent=2))
    typer.echo(f"missing: {rec.missing_core()}")


def ask_user(status: SessionStatus, url: str) -> bool:
    """Pause until the user has dealt with a captcha or sign-in in the browser window."""
    typer.echo(f"\n{status.detail} at {url}")
    typer.echo("Solve it or log in in the product-scout browser window.")
    answer = typer.prompt("Press Enter when done, or type s and Enter to skip", default="", show_default=False)
    return answer.strip().lower() != "s"


@app.command()
def crawl(
    category: str,
    query: str,
    pages: int = 1,
    details: int = 20,
    budget: Optional[float] = None,
    screenshots: bool = False,
    no_llm: bool = typer.Option(False, "--no-llm"),
) -> None:
    """Search amazon.se, fetch detail pages and store them. QUERY is in Swedish, e.g. kylskåp."""
    settings = load_settings()
    budget_sek = Decimal(str(budget)) if budget is not None else None
    enrich, screen, llm = None, None, None
    if not no_llm:
        profile = load_profile(category)
        llm = LLM(load_llm_config())
        enrich = lambda rec: enrich_record(rec, profile, llm)  # noqa: E731
        screen = lambda cands: screen_titles(cands, profile, llm)  # noqa: E731
    with connect(settings.database_url) as conn, BrowserSession(settings.browser_profile_dir) as session:
        adapter = AmazonSeAdapter(session, settings.snapshot_dir, screenshots=screenshots)
        report = run_crawl(conn, adapter, category, query, on_problem=ask_user,
                           max_pages=pages, max_details=details, budget=budget_sek, enrich=enrich, screen=screen)
    typer.echo(f"search pages {report.search_pages}, candidates {report.candidates}, "
               f"over budget {report.over_budget}, detail pages {report.detail_pages}, saved {report.saved}")
    typer.echo(f"dropped as off topic by title: {report.off_topic}")
    for cand in report.dropped:
        typer.echo(f"  dropped {cand.source_id} {cand.title[:90]}")
    typer.echo(f"pages sent to the worker model: {report.llm_pages} of {report.detail_pages}")
    typer.echo(f"fields the selectors missed: {dict(report.missing) or 'none'}")
    for url in report.skipped:
        typer.echo(f"skipped {url}")
    for note in report.notes:
        typer.echo(note)
    if llm is not None:
        report_llm_use(llm)


def report_llm_use(llm: LLM) -> None:
    for role, reason in llm.fallbacks.items():
        typer.echo(f"{role} moved to {llm.model_for(role)}: {reason}")
    for model in dict.fromkeys(c.model for c in llm.calls):
        calls = [c for c in llm.calls if c.model == model]
        typer.echo(f"LLM {model}: calls {len(calls)}, tokens in {sum(c.input_tokens for c in calls)}, "
                   f"out {sum(c.output_tokens for c in calls)}")


@app.command()
def picks(
    category: str,
    budget: Optional[float] = None,
    where: Optional[List[str]] = typer.Option(None, "--where", help="e.g. height<=180, repeatable"),
    k: int = 5,
    no_llm: bool = typer.Option(False, "--no-llm"),
    lang: str = "English",
) -> None:
    """Rank stored products, recheck the top ones on amazon.se, and print the picks."""
    settings = load_settings()
    profile = load_profile(category)
    conditions = [parse_condition(w) for w in where or []]
    for cond in conditions:
        if profile.attribute(cond.attribute) is None:
            raise typer.BadParameter(f"'{cond.attribute}' is not in the {category} profile")
    budget_sek = Decimal(str(budget)) if budget is not None else None
    conditions_text = ", ".join([*(where or []), *([f"budget {budget:g} SEK"] if budget_sek else [])])
    llm = None if no_llm else LLM(load_llm_config())
    notes: list[str] = []
    with connect(settings.database_url) as conn:
        cards = load_cards(conn, category, budget_sek)
        size_notes = drop_implausible_sizes(cards)
        cards, dropped = apply_conditions(cards, conditions)
        ranked = rank(cards)
        if llm is None:
            notes.append("Titles were not screened, so items outside the category can appear.")
        elif ranked:
            try:
                on_topic = screen_titles(ranked, profile, llm)
                if len(on_topic) < len(ranked):
                    notes.append(f"{len(ranked) - len(on_topic)} stored items are not a {profile.display_name.lower()} "
                                 "by their titles and were left out.")
                ranked = on_topic
            except (LLMUnavailable, LLMOutputError) as exc:
                notes.append(f"Title screening skipped: {exc}")
        if not ranked:
            typer.echo("No stored product meets these conditions. Run scout crawl first, or loosen them.")
            raise typer.Exit(1)
        with BrowserSession(settings.browser_profile_dir) as session:
            adapter = AmazonSeAdapter(session, settings.snapshot_dir)

            def refetch(card):
                page = fetch_checked(adapter, conn, card.url, ask_user)
                if page is None:
                    return None
                rec = adapter.parse(page)
                save_record(conn, rec, category)
                return rec

            chosen, recheck_notes = recheck_picks(ranked, k, refetch, budget_sek, conditions)
    notes += recheck_notes + [size_notes[c.listing_id] for c in chosen if c.listing_id in size_notes]
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            texts, errors = write_picks(llm, chosen, conditions_text, lang)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    typer.echo(format_picks(chosen, texts, notes, conditions_text, dropped, errors))
    if llm is not None:
        report_llm_use(llm)


@app.command("eval-template")
def eval_template(out: Path = Path("data/eval/price_labels.csv")) -> None:
    """Write a label sheet with one row per saved product snapshot."""
    n = write_template(load_settings().snapshot_dir, out)
    typer.echo(f"{n} rows written to {out}. Fill true_price (or none), true_currency and true_in_stock (yes or no).")


@app.command("eval-prices")
def eval_prices(
    labels: Path = Path("data/eval/price_labels.csv"),
    llm: bool = typer.Option(False, "--llm"),
    category: str = "fridge",
) -> None:
    """Measure price and stock extraction against your labels and save the numbers."""
    rows = read_labels(labels)
    extract, extractor = extract_from_snapshot, "selectors"
    if llm:
        load_settings()
        worker = LLM(load_llm_config())
        profile = load_profile(category)
        extract = lambda label: enrich_record(extract_from_snapshot(label), profile, worker)[0]  # noqa: E731
        extractor = f"selectors+worker:{worker.model_for('worker')}"
    result = evaluate_prices(rows, extract)
    summary = {"extractor": extractor, "run_at": datetime.now(timezone.utc).isoformat(), **result.summary()}
    out = ROOT / "eval" / "results" / f"prices-{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))
    for failure in result.failures:
        typer.echo(f"wrong: {failure}")
    typer.echo(f"saved {out}")


class _PingItem(BaseModel):
    name: str
    value: str | None = None


class _Ping(BaseModel):
    items: list[_PingItem]


@app.command("llm-ping")
def llm_ping() -> None:
    """Check the API keys and the configured models, and make one small structured call per role and fallback."""
    load_settings()  # loads .env
    config = load_llm_config()
    llm = LLM(config)
    used = {config.roles[r].model for r in config.roles} | {config.roles[r].fallback.model
                                                               for r in config.roles if config.roles[r].fallback}
    for name in config.providers:
        try:
            ids = {model.id.removeprefix("models/") for model in llm.client(name).models.list()}
        except RuntimeError as exc:  # no key for this provider
            typer.echo(f"{name}: {exc}")
            continue
        typer.echo(f"{name}: {len(ids)} models; configured and present: {sorted(ids & used) or 'none'}")
    for role in ("chat", "worker"):
        # Nested models and an optional field, the same shapes the extraction and picks schemas use.
        ask = "Return two items: name ping with value pong, and name empty with value null."
        reply = llm.structured(role, "Reply in JSON.", ask, _Ping)
        typer.echo(f"{role} ({llm.model_for(role)}): {[(i.name, i.value) for i in reply.items]}")
        fallback = config.roles[role].fallback
        if fallback is not None:
            reply = llm._structured(role, fallback, "Reply in JSON.", ask, _Ping)
            typer.echo(f"{role} fallback ({fallback.model}): {[(i.name, i.value) for i in reply.items]}")


@app.command("trace-ping")
def trace_ping() -> None:
    """Check the Langfuse keys and send one test trace."""
    load_settings()  # loads .env
    tracer = tracer_from_env()
    if not tracer.enabled:
        typer.echo("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set, so tracing is off.")
        raise typer.Exit(1)
    typer.echo(f"keys accepted: {tracer.auth_check()}")
    with tracer.turn("trace-ping", "ping") as span:
        span.update(output="pong")
    tracer.flush()
    typer.echo("Sent one trace named 'chat turn' in session 'trace-ping'.")
