"""Command line for product-scout."""
import contextlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import typer
import yaml
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel

from scout.agent.graph import build_graph
from scout.agent.history import assistant
from scout.agent.live import LazyBrowser, live_deps, loop_deps
from scout.agent.loop import build_agent_graph, last_node
from scout.config import ROOT, load_settings
from scout.crawl import crawl as run_crawl
from scout.crawl import fetch_checked
from scout.db.conn import connect, connect_checkpoints, empty_tables, end_transaction, init_schema
from scout.db.store import save_page_text, save_record
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.eval.grade import check_grader, good_enough, grade_results, grade_turn, grades_text
from scout.eval.hidden import LIMITS, MAX_SHOPPER_TURNS, load_targets, run_target
from scout.eval.hidden import summarize as summarize_hidden
from scout.eval.hidden import summary_text as hidden_text
from scout.eval.prices import evaluate_prices, extract_from_snapshot, read_labels, write_template
from scout.llm.client import LLM, LLMOutputError, LLMUnavailable
from scout.llm.config import LLMConfig, RoleConfig, load_llm_config
from scout.needs import ConditionSpec, Needs, check_needs
from scout.picks.pipeline import make_picks, refetcher, shortlist
from scout.picks.rank import parse_condition
from scout.profiles import load_profile
from scout.reviews import read_reviews
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.reviews import parse_review_page
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


@app.command("backfill-text")
def backfill_text() -> None:
    """Store the description, the reviews and the image links of each stored product from its newest snapshot.
    No browser opens."""
    settings = load_settings()
    with connect(settings.database_url) as conn:
        init_schema(conn)
        listings = conn.execute("SELECT id, source_id FROM listings WHERE source = %s", (SOURCE,)).fetchall()
        done = 0
        for row in listings:
            pages = sorted((settings.snapshot_dir / SOURCE / "product" / row["source_id"]).glob("*.html"))
            if not pages:
                continue
            fetched = datetime.strptime(pages[-1].stem, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            rec = parse_product_page(pages[-1].read_text(encoding="utf-8"), product_url(row["source_id"]), fetched)
            save_page_text(conn, row["id"], rec)
            done += 1
        conn.commit()
    typer.echo(f"Stored the page text of {done} of {len(listings)} listings.")


@app.command()
def embed() -> None:
    """Give each stored chunk its bge-m3 vector and its BM25 tokens. bge-m3 runs on this computer."""
    from scout.text.embed import BgeM3Embedder
    from scout.text.index import index_chunks

    settings = load_settings()
    embedder = BgeM3Embedder()
    with connect(settings.database_url) as conn:
        init_schema(conn)
        report = index_chunks(conn, embedder)
    typer.echo(f"Embedded {report.chunks} chunks with {embedder.name} in {report.seconds:.1f} s.")


@app.command("make-retrieval-questions")
def make_retrieval_questions(
    one: int = typer.Option(12, help="products with questions about one product"),
    per_product: int = typer.Option(2, help="questions for each of these products"),
    across: int = typer.Option(6, help="groups of 3 products with one question across them"),
    seed: int = typer.Option(7, help="the seed that chooses the products"),
    force: bool = typer.Option(False, help="replace the question file when it exists"),
    yes: bool = typer.Option(False, help="start without the question"),
) -> None:
    """An LLM (the chat role) writes the questions of eval-retrieval from the stored chunks; Python keeps a gold
    label only when its words are in its chunk. Writes eval/retrieval/questions.yaml. Costs a few cents."""
    import yaml

    from scout.eval.make_questions import make_questions
    from scout.eval.retrieval import QUESTIONS

    if QUESTIONS.exists() and not force:
        typer.echo(f"{QUESTIONS} exists. Use --force to replace it.")
        raise typer.Exit(1)
    settings = load_settings()
    llm = LLM(load_llm_config())
    if not yes and not typer.confirm(f"{one + across} calls to {llm.model_for('chat')}, and one more for the search "
                                     "words of each question. Start?"):
        raise typer.Exit(1)
    with connect(settings.database_url) as conn:
        made = make_questions(conn, llm, one, per_product, across, seed, say=typer.echo)
    for problem in made.problems:
        typer.echo(f"Not used: {problem}")
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)
    QUESTIONS.write_text(yaml.safe_dump(made.questions, allow_unicode=True, sort_keys=False, width=120),
                         encoding="utf-8")
    gold = sum(len(q["gold"]) for q in made.questions)
    typer.echo(f"{len(made.questions)} of {made.asked} questions kept, {gold} gold chunks. Saved {QUESTIONS}")
    report_llm_use(llm)


@app.command("rewrite-retrieval-words")
def rewrite_retrieval_words(yes: bool = typer.Option(False, help="start without the question")) -> None:
    """The chat role writes the search words of each question in eval/retrieval/questions.yaml again, from the
    question only. The questions and the gold labels stay. Costs about one cent."""
    import yaml

    from scout.eval.make_questions import search_words
    from scout.eval.retrieval import QUESTIONS

    rows = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    llm = LLM(load_llm_config())
    if not yes and not typer.confirm(f"{len(rows)} calls to {llm.model_for('chat')}. Start?"):
        raise typer.Exit(1)
    for row in rows:
        row["queries"] = search_words(llm, "chat", row["question"])
        typer.echo(f"{row['id']}: {row['queries']}")
    QUESTIONS.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    report_llm_use(llm)


@app.command("eval-retrieval")
def eval_retrieval(k: int = 10) -> None:
    """Compare the retrieval methods on eval/retrieval/questions.yaml. No API calls: bge-m3 runs on this computer."""
    from scout.eval.retrieval import gold_ids, load_questions, score_method, scores_table
    from scout.text.embed import BgeM3Embedder
    from scout.text.index import index_chunks
    from scout.text.retrieve import (Bm25Retriever, HybridRetriever, LongContext, MultiQueryRetriever,
                                     SparseRetriever, VectorRetriever)

    settings = load_settings()
    questions = load_questions()
    embedder = BgeM3Embedder()
    with connect(settings.database_url) as conn:
        init_schema(conn)
        index_chunks(conn, embedder)
        problems = [p for q in questions for p in gold_ids(conn, q)[1]]
        for p in problems:
            typer.echo(f"Not used: {p}")
        vector, bm25 = VectorRetriever(conn, embedder), Bm25Retriever(conn, embedder)
        methods = [vector, bm25, SparseRetriever(conn, embedder), HybridRetriever([vector, bm25]),
                   MultiQueryRetriever([vector]), MultiQueryRetriever([vector, bm25]), LongContext(conn)]
        scores = [score_method(conn, m, questions, embedder.count, k) for m in methods]
        chunks = conn.execute("SELECT count(*) AS n, count(DISTINCT listing_id) AS l FROM chunks").fetchone()
    typer.echo(f"{len(questions)} questions, {chunks['n']} chunks of {chunks['l']} listings.\n")
    typer.echo(scores_table(scores, k))
    for s in scores:
        if s.misses:
            typer.echo(f"{s.method} found no gold chunk for: {', '.join(s.misses)}")
    out = ROOT / "eval" / "results" / f"retrieval-{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"k": k, "chunks": chunks["n"], "listings": chunks["l"], "problems": problems,
                               "scores": [s.__dict__ for s in scores]}, indent=2), encoding="utf-8")
    typer.echo(f"Saved {out}")


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


@app.command("parse-reviews")
def parse_reviews_cmd(path: Path) -> None:
    """Print what the parser reads from a saved review page."""
    reviews, matching = parse_review_page(path.read_text(encoding="utf-8"))
    for r in reviews:
        typer.echo(json.dumps(r, ensure_ascii=False))
    typer.echo(f"{len(reviews)} reviews on the page, {matching} match the filter")


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
        costs = [c.cost for c in calls if c.cost is not None]
        typer.echo(f"LLM {model}: calls {len(calls)}, tokens in {sum(c.input_tokens for c in calls)}, "
                   f"out {sum(c.output_tokens for c in calls)}"
                   + (f", cost {sum(costs):.4f} USD" if costs else ""))


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
    needs = Needs(category=category, budget=budget,
                  conditions=[ConditionSpec(attribute=c.attribute, op=c.op, value=c.value) for c in conditions])
    for error in check_needs(needs, profile):
        raise typer.BadParameter(error)
    llm = None if no_llm else LLM(load_llm_config())
    with connect(settings.database_url) as conn:
        short = shortlist(conn, profile, needs, llm)
        if not short.ranked:
            typer.echo("No stored product meets these conditions. Run scout crawl first, or loosen them.")
            raise typer.Exit(1)
        with BrowserSession(settings.browser_profile_dir) as session:
            adapter = AmazonSeAdapter(session, settings.snapshot_dir)
            result = make_picks(short, needs, k, refetcher(adapter, conn, category, ask_user), llm, lang)
    typer.echo(result.render())
    if llm is not None:
        report_llm_use(llm)


@app.command()
def reviews(asins: List[str], max_pages: int = 30) -> None:
    """Read the positive and the critical review pages of stored amazon.se products. Needs the login."""
    settings = load_settings()
    with connect(settings.database_url) as conn:
        rows = conn.execute("SELECT id, source_id FROM listings WHERE source = %s AND source_id = ANY(%s)",
                            (SOURCE, list(asins))).fetchall()
        found = {row["source_id"]: row["id"] for row in rows}
        for asin in asins:
            if asin not in found:
                typer.echo(f"{asin} is not stored. Crawl it first.")
        browser = LazyBrowser(settings)
        try:
            report = read_reviews(conn, browser.adapter, [(found[a], a) for a in asins if a in found], ask_user,
                                  max_pages=max_pages, say=typer.echo)
        finally:
            browser.close()
    typer.echo(report.text() or "Nothing to read: the review pages of these products were read in the last 7 days.")


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


@app.command()
def chat(thread: Optional[str] = typer.Option(None, help="the ID of a conversation to continue"),
         k: int = 5, pipeline: bool = typer.Option(False, help="the fixed pipeline of before the agent loop")) -> None:
    """Talk with the agent. It clarifies your needs, searches amazon.se and answers follow-up questions."""
    settings = load_settings()
    tracer = tracer_from_env()
    llm = LLM(load_llm_config(), tracer=tracer)
    thread_id = thread or uuid.uuid4().hex[:8]
    config = {"configurable": {"thread_id": thread_id}}
    browser = LazyBrowser(settings)
    with connect(settings.database_url) as conn, connect_checkpoints(settings.database_url) as cp_conn:
        init_schema(conn)  # new tables, for example the memory of the shopper
        saver = PostgresSaver(cp_conn)
        saver.setup()
        if pipeline:
            graph = build_graph(live_deps(conn, llm, browser, ask_user, typer.echo, tracer, k), saver)
        else:
            graph = build_agent_graph(loop_deps(conn, llm, browser, ask_user, typer.echo, tracer), saver)
        messages = graph.get_state(config).values.get("messages", [])
        if messages:
            typer.echo(f"Continuing conversation {thread_id}. The last reply was:\n\n{messages[-1]['content']}")
        else:
            typer.echo(f"Conversation {thread_id}. To continue it later: scout chat --thread {thread_id}")
        typer.echo("Type q to stop.")
        try:
            while True:
                text = typer.prompt("\nyou", prompt_suffix="> ").strip()
                if text.lower() in {"q", "quit", "exit"}:
                    break
                replies = []
                try:
                    with tracer.turn(thread_id, text) as span:
                        # Each step prints its replies when it ends, so the picks show before the reviews are read
                        for update in graph.stream({"messages": [{"role": "user", "content": text}]}, config,
                                                   stream_mode="updates"):
                            for change in update.values():
                                for message in (change or {}).get("messages", []):
                                    if message.get("role") == "assistant":
                                        replies.append(message["content"])
                                        typer.echo(f"\n{message['content']}")
                        span.update(output="\n\n".join(replies))
                except Exception as exc:  # the nodes catch most problems; this keeps the conversation open
                    message = f"Something went wrong ({type(exc).__name__}: {exc}). You can try again."
                    end_transaction(conn)
                    with contextlib.suppress(Exception):  # the thread gets the message, and the failed turn ends
                        graph.update_state(config, {"messages": [assistant(message)]}, as_node=last_node(graph))
                    typer.echo(f"\n{message}")
                end_transaction(conn)
        finally:
            browser.close()
            tracer.flush()
    report_llm_use(llm)


@app.command()
def web(port: int = 9000, k: int = 5,
        pipeline: bool = typer.Option(False, help="the fixed pipeline of before the agent loop")) -> None:
    """Open the chat page at http://127.0.0.1:PORT. It uses the same threads as scout chat."""
    import uvicorn

    from scout.web.app import create_app
    from scout.web.service import ChatService

    settings = load_settings()
    tracer = tracer_from_env()
    llm = LLM(load_llm_config(), tracer=tracer)
    browser = LazyBrowser(settings)
    with connect(settings.database_url) as conn, connect_checkpoints(settings.database_url) as cp_conn:
        init_schema(conn)  # new tables, for example the memory of the shopper
        saver = PostgresSaver(cp_conn)
        saver.setup()

        def build(say, on_problem):
            if pipeline:
                deps = live_deps(conn, llm, browser, on_problem, say, tracer, k)
                return build_graph(deps, saver), deps
            deps = loop_deps(conn, llm, browser, on_problem, say, tracer)
            return build_agent_graph(deps, saver), deps

        service = ChatService(build, lambda: end_transaction(conn))
        typer.echo(f"Open http://127.0.0.1:{port} in your browser. Press Ctrl+C to stop.")
        try:
            uvicorn.run(create_app(service), host="127.0.0.1", port=port, log_level="warning")
        finally:
            service.in_worker(browser.close)
            tracer.flush()
    report_llm_use(llm)


@app.command()
def threads(limit: int = 20) -> None:
    """List the stored conversations, newest first."""
    with connect_checkpoints(load_settings().database_url) as cp_conn:
        PostgresSaver(cp_conn).setup()
        rows = cp_conn.execute(
            "SELECT thread_id, max(checkpoint->>'ts') AS last FROM checkpoints "
            "GROUP BY thread_id ORDER BY last DESC LIMIT %s", (limit,)).fetchall()
    for row in rows:
        typer.echo(f"{row['thread_id']}  last turn {row['last'][:16]} UTC")


def judge_llm(judge: Optional[str], rpm: int = 60) -> LLM:
    """The LLM with the judge role from models.yaml, or from --judge provider:model. Stops when it cannot run."""
    load_settings()  # reads .env, which has the API keys
    config = load_llm_config()
    try:
        if judge:
            provider, model = judge.split(":", 1)
            config.roles["judge"] = RoleConfig(provider=provider, model=model, rpm=rpm)
        if "judge" not in config.roles:
            raise ValueError("models.yaml has no judge role")
        config = LLMConfig.model_validate(config.model_dump())
        llm = LLM(config)
        llm.client(config.roles["judge"].provider)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Cannot run: {exc}")
        raise typer.Exit(1)
    return llm


@app.command("grade")
def grade(
    results: Path,
    judge: Optional[str] = typer.Option(None, help="provider:model for the grader, for example "
                                                   "gemini:gemini-3.5-flash-lite"),
    rpm: int = typer.Option(60, help="requests per minute for --judge (free OpenRouter models: 20)"),
) -> None:
    """Grade the answers of a saved eval-hidden results file. Writes <name>-graded.json next to it."""
    llm = judge_llm(judge, rpm)
    try:
        data = grade_results(llm, json.loads(results.read_text(encoding="utf-8")), typer.echo)
    except ValueError as exc:
        typer.echo(f"Cannot grade: {exc}")
        raise typer.Exit(1)
    out = results.with_name(f"{results.stem}-graded.json")
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(grades_text(data["summary"]["grades"]))
    report_llm_use(llm)
    typer.echo(f"saved {out}")


@app.command("check-grader")
def check_grader_command(
    items: Path = ROOT / "eval" / "grader" / "known_faults.yaml",
    judge: Optional[str] = typer.Option(None, help="provider:model for the grader"),
    rpm: int = typer.Option(60, help="requests per minute for --judge (free OpenRouter models: 20)"),
) -> None:
    """Grade the test set with planted faults, and show how many faults the grader found and how fast it was."""
    import time
    llm = judge_llm(judge, rpm)
    test_set = yaml.safe_load(items.read_text(encoding="utf-8"))
    started = time.monotonic()
    grades = [grade_turn(llm, item, item.get("history") or []) for item in test_set]
    seconds = time.monotonic() - started
    import statistics
    median = statistics.median(c.seconds for c in llm.calls) if llm.calls else 0
    typer.echo(f"Grader: {llm.model_for('judge')}, {len(test_set)} items, {seconds:.0f} s in all, "
               f"median {median:.1f} s a call, {len(llm.calls)} calls")
    rows = check_grader(test_set, grades)
    slug = re.sub(r"[^\w.-]", "_", llm.model_for("judge"))
    out = ROOT / "eval" / "results" / f"grader-{slug}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"judge": llm.model_for("judge"), "seconds": round(seconds, 1),
                               "median_call_seconds": round(median, 1), "rows": rows,
                               "grades": {item["id"]: grade.model_dump() for item, grade in zip(test_set, grades)}},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    for name, row in rows.items():
        typer.echo(f"  {name}: found {row['found']} of {row['faults']} planted faults, failed {row['failed']} of "
                   f"{row['correct']} correct replies, {'good enough' if good_enough(row) else 'NOT good enough'}"
                   + (f", missed {', '.join(row['missed'])}" if row["missed"] else "")
                   + (f", wrongly failed {', '.join(row['wrongly_failed'])}" if row["wrongly_failed"] else ""))
    report_llm_use(llm)
    typer.echo(f"saved {out}")


@app.command("eval-hidden")
def eval_hidden(
    targets: Path = ROOT / "eval" / "hidden" / "targets.yaml",
    only: Optional[str] = typer.Option(None, help="run only the targets with these ids, separated by commas"),
    shopper: Optional[str] = typer.Option(None, help="provider:model for the simulated shopper"),
    chat: Optional[str] = typer.Option(None, help="provider:model for the chat role, for example "
                                                  "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"),
    worker: Optional[str] = typer.Option(None, help="provider:model for the worker role"),
    rpm: int = typer.Option(60, help="requests per minute for the models given here (free OpenRouter models: 20)"),
    turns: int = typer.Option(MAX_SHOPPER_TURNS, help="the most shopper messages after the first one"),
    keep: bool = typer.Option(False, help="keep the products of the earlier targets in the evaluation database"),
    yes: bool = typer.Option(False, help="start without the question"),
) -> None:
    """The hidden-target test: a simulated shopper wants one real product, and Luna must find it on amazon.se.
    Uses the evaluation database (EVAL_DATABASE_URL), which is emptied before each target. Costs money."""
    from functools import cache

    from langgraph.checkpoint.memory import InMemorySaver

    from scout.text.embed import BgeM3Embedder

    settings = load_settings()
    config = load_llm_config()
    for role, given in (("chat", chat), ("worker", worker), ("shopper", shopper)):
        if given:
            provider, model = given.split(":", 1)
            config.roles[role] = RoleConfig(provider=provider, model=model, rpm=rpm)
    config.roles["chat"].fallback = None  # one chat model for each run, so the numbers belong to it
    all_targets = load_targets(targets)
    unknown = set(only.split(",")) - {t.id for t in all_targets} if only else set()
    try:
        if settings.eval_database_url == settings.database_url:
            raise ValueError("EVAL_DATABASE_URL is the main database; it is emptied before each target")
        if "shopper" not in config.roles:
            raise ValueError("models.yaml has no shopper role")
        if unknown:
            raise ValueError(f"no targets with the ids {sorted(unknown)}")
        config = LLMConfig.model_validate(config.model_dump())
        tracer = tracer_from_env()
        llm = LLM(config, tracer=tracer)
        for role in ("chat", "worker", "shopper"):
            llm.client(config.roles[role].provider)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Cannot run: {exc}")
        raise typer.Exit(1)
    todo = [t for t in all_targets if only is None or t.id in only.split(",")]
    typer.echo(f"{len(todo)} targets, chat {llm.model_for('chat')}, shopper {llm.model_for('shopper')}. Each target "
               f"opens amazon.se in the product-scout browser and costs money.")
    if not yes and not typer.confirm("Start?"):
        raise typer.Exit(0)
    browser = LazyBrowser(settings)
    embedder = cache(BgeM3Embedder)  # bge-m3 loads once for all targets
    records = []
    try:
        with connect(settings.eval_database_url) as conn:
            init_schema(conn)
            for target in todo:
                if not keep:
                    empty_tables(conn)
                deps = loop_deps(conn, llm, browser, ask_user, lambda text: typer.echo(f"  {text}"), tracer,
                                 make_embedder=embedder,
                                 # a new shopper for each target and run: no memory of other targets or runs
                                 shopper=f"eval-{target.id}-{uuid.uuid4().hex[:8]}")
                graph = build_agent_graph(deps, InMemorySaver())

                def source_ids(ids):
                    rows = conn.execute("SELECT id, source_id FROM listings WHERE id = ANY(%s)", (ids,)).fetchall()
                    by_id = {r["id"]: r["source_id"] for r in rows}
                    return [by_id.get(i, "") for i in ids]

                def seen(t):
                    return conn.execute("SELECT 1 FROM listings WHERE source_id = ANY(%s)",
                                        (sorted(t.ids()),)).fetchone() is not None

                typer.echo(f"\n=== {target.id}: {target.opening}")
                records.append(run_target(target, graph, llm, source_ids, seen, turns, typer.echo,
                                          after_turn=lambda: end_transaction(conn)))
                end_transaction(conn)
                r = records[-1]
                typer.echo(f"{target.id}: {'found at place ' + str(r['rank']) if r['found'] else 'not found'}"
                           f"{', stored' if r['seen'] else ''}, {r['totals']['seconds']} s, "
                           f"{r['totals']['cost_usd']:.4f} USD" + (f", error {r['error']}" if r["error"] else ""))
    finally:
        browser.close()
        tracer.flush()
    summary = {"chat_model": llm.model_for("chat"), "worker_model": llm.model_for("worker"),
               "shopper_model": llm.model_for("shopper"), "run_at": datetime.now(timezone.utc).isoformat(),
               "limits": LIMITS, **summarize_hidden(records)}
    slug = re.sub(r"[^\w.-]", "_", llm.model_for("chat"))
    out = ROOT / "eval" / "results" / f"hidden-{slug}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"summary": summary, "runs": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(hidden_text(summary))
    report_llm_use(llm)
    typer.echo(f"saved {out}")
