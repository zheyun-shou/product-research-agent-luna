// The web chat page: one thread in the URL, the events of a turn as lines of JSON, and the picks panel.
const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), send = $("send"), cards = $("cards");
let thread = null, busy = false, picks = [];

function newThread() {
  return Math.random().toString(16).slice(2, 10);
}

function threadFromHash() {
  const match = location.hash.match(/^#t=([A-Za-z0-9_-]{1,64})$/);
  return match ? match[1] : null;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Plain text with links. The replies keep their line breaks and indents (white-space: pre-wrap).
function linked(text) {
  const frag = document.createDocumentFragment();
  const url = /https?:\/\/[^\s)\]]+/g;
  let last = 0, m;
  while ((m = url.exec(text))) {
    frag.append(text.slice(last, m.index));
    const a = el("a", null, m[0]);
    a.href = m[0]; a.target = "_blank"; a.rel = "noopener";
    frag.append(a);
    last = m.index + m[0].length;
  }
  frag.append(text.slice(last));
  return frag;
}

// The text of a failed request: the page's own text, or the detail of the server (in English) on an English page
async function errorText(response, fallback) {
  if (response.status === 409) return tr("busy");
  let detail = null;
  try { detail = (await response.json()).detail; } catch (e) { /* no JSON body */ }
  if (lang === "en" && typeof detail === "string" && detail) return detail;
  return fallback ? tr(fallback) : tr("server", { status: response.status });
}

function scrollDown() {
  log.scrollTop = log.scrollHeight;
}

function addMessage(role, text) {
  document.body.classList.remove("empty");
  const node = el("div", `msg ${role}`);
  node.append(linked(text));
  log.append(node);
  scrollDown();
  return node;
}

function setBusy(on) {
  busy = on;
  send.disabled = on || !input.value.trim();
  input.placeholder = tr(on ? "wait_hint" : document.body.classList.contains("empty") ? "first_hint" : "next_hint");
}

function grow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
  send.disabled = busy || !input.value.trim();
}

// Picks panel
function openPanel(open) {
  document.body.classList.toggle("panel-open", open && picks.length > 0);
  if (open && picks.length > 0 && NARROW.matches) openThreads(false);
}

function chip(attr) {
  const node = el("span", attr.doubtful ? "chip doubtful" : "chip", `${attr.name} ${attr.text}`);
  if (attr.doubtful) node.title = tr("doubtful");
  return node;
}

function card(pick) {
  const item = el("li", "card");
  const thumb = el("div", "thumb");
  if (pick.image) {
    const img = el("img");
    img.src = pick.image; img.alt = ""; img.loading = "lazy"; img.referrerPolicy = "no-referrer";
    thumb.append(img);
  } else {
    thumb.append(el("span", null, tr("no_image")));
  }
  const body = el("div", "card-body");
  const title = el("a", "card-title");
  title.href = pick.url; title.target = "_blank"; title.rel = "noopener";
  title.append(el("span", "num", String(pick.number)), pick.title);
  title.title = pick.title;
  const facts = el("div", "facts");
  facts.append(el("span", "price", pick.price || tr("price_unknown")));
  facts.append(el("span", null, pick.rating ? `★ ${pick.rating} (${pick.review_count ?? 0})` : tr("no_ratings")));
  if (pick.in_stock === false) facts.append(el("span", null, tr("sold_out")));
  body.append(title, facts);
  if (pick.attributes.length) {
    const chips = el("div", "chips");
    pick.attributes.forEach((a) => chips.append(chip(a)));
    body.append(chips);
  }
  if (pick.note) body.append(el("div", "card-note", pick.note));
  const del = el("button", "icon delete");
  del.type = "button";
  del.setAttribute("aria-label", tr("delete_pick", { n: pick.number }));
  del.title = tr("delete_this");
  del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  del.addEventListener("click", () => removePick(pick, item));
  item.append(thumb, body, del);
  return item;
}

function showPicks(data, open) {
  const before = picks.length;
  picks = data.picks || [];
  cards.replaceChildren(...picks.map(card));
  $("picks-count").textContent = picks.length;
  $("picks-toggle").hidden = picks.length === 0;
  const removed = data.removed || [];
  $("removed").hidden = removed.length === 0;
  $("removed-count").textContent = removed.length;
  $("removed-list").replaceChildren(...removed.map((r) => el("li", null, r.title)));
  if (open && picks.length && (before === 0 || window.innerWidth > 900)) openPanel(true);
  if (!picks.length) openPanel(false);
}

async function removePick(pick, item) {
  if (busy) return;
  item.classList.add("leaving");
  const response = await fetch(`/api/threads/${thread}/picks/${pick.product_id}`, { method: "DELETE" });
  if (!response.ok) {
    item.classList.remove("leaving");
    addMessage("error", await errorText(response, "not_deleted"));
    return;
  }
  const data = await response.json();
  addMessage("assistant", data.message);
  showPicks(data, false);
}

// Login or captcha pause
function showProblem(event) {
  const known = { signin: "signin", captcha: "captcha" };
  // Other problems keep the detail of the server (in English), for example the status of the page
  const detail = known[event.problem] ? tr(known[event.problem])
    : lang === "en" ? event.detail || tr("other_problem") : `${tr("other_problem")} (${event.detail || event.problem})`;
  $("problem-detail").textContent = tr("needs_you", { detail });
  $("problem-url").href = event.url;
  $("problem").hidden = false;
}

async function answerProblem(skip) {
  $("problem").hidden = true;
  await fetch("/api/problem", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skip }) });
}

// The progress of a turn: one line for each step of the agent, with a clock and the limits of the turn
const ICONS = {
  think: "M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1",
  needs: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  shop: "M3 4h2l2.4 11h10.2L20 8H6.2M9 20h.01M17 20h.01",
  web: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3",
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  reviews: "M4 5h16v11H9l-5 4z",
  check: "M12 3l8 3v6c0 4.4-3.4 8-8 9-4.6-1-8-4.6-8-9V6zM8.5 12l2.5 2.5 4.5-5",
  note: "M12 8v5M12 16h.01",
};
const METER = ["calls", "product_pages", "web_searches"];

function clock(seconds) {
  const s = Math.round(seconds);
  return s < 60 ? tr("sec", { s }) : tr("min", { m: Math.floor(s / 60), s: String(s % 60).padStart(2, "0") });
}

function icon(kind) {
  const span = el("span", "s-icon");
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[kind] || ICONS.note}"/></svg>`;
  return span;
}

function progress() {
  const box = el("details", "progress");
  box.open = true;
  const summary = el("summary");
  const title = el("span", "p-title", tr("working"));
  const time = el("span", "p-time", clock(0));
  const meter = el("span", "p-meter");
  summary.append(el("span", "p-spin"), title, time, meter);
  const list = el("ol", "steps");
  box.append(summary, list);
  const p = { box, list, title, time, meter, steps: new Map(), count: 0, started: Date.now() };
  p.timer = setInterval(() => { time.textContent = clock((Date.now() - p.started) / 1000); }, 1000);
  return p;
}

function running(p) {
  return [...p.list.children].filter((li) => li.classList.contains("run"));
}

// A planning step shows only while the model thinks; the next step replaces it
function settle(p) {
  running(p).forEach((li) => {
    if (li.classList.contains("think")) li.remove();
    else li.classList.replace("run", "done");
  });
}

function addStep(p, kind, title, counted = kind !== "think") {
  const li = el("li", `step ${kind} run`);
  const body = el("div", "s-body");
  body.append(el("div", "s-title", title), el("div", "s-lines"), el("div", "s-detail"));
  li.append(icon(kind), body);
  p.list.append(li);
  if (counted) p.count += 1;
  return li;
}

function onStep(p, event) {
  let li = p.steps.get(event.id);
  if (!li) {
    settle(p);
    li = addStep(p, event.kind, event.title);
    p.steps.set(event.id, li);
  }
  li.classList.remove("run", "done", "fail");
  li.classList.add(event.state);
  if (event.detail !== undefined || event.seconds !== undefined) {
    const parts = [event.detail, event.seconds >= 1 ? clock(event.seconds) : null].filter(Boolean);
    li.querySelector(".s-detail").textContent = parts.join(" · ");
  }
  if (event.used) {
    p.meter.textContent = METER.map((key) => `${tr(key)} ${event.used[key]}/${event.limits[key]}`).join(" · ");
  }
}

// A status line of a tool (the search pages, the login) goes under the running step
function onStatus(p, text) {
  const li = running(p).filter((x) => !x.classList.contains("think")).pop();
  if (li) li.querySelector(".s-lines").append(el("div", null, text));
  // Without a running tool the line is a note of its own: not a step, and the planning step stays
  else addStep(p, "note", text, false).classList.replace("run", "done");
}

function finish(p) {
  clearInterval(p.timer);
  settle(p);
  if (!p.list.childElementCount) { p.box.remove(); return; }
  p.box.classList.add("finished");
  p.title.textContent = tr("worked", { time: clock((Date.now() - p.started) / 1000) });
  p.time.textContent = p.count === 1 ? tr("step_one") : tr("steps", { n: p.count });
  p.box.open = false;
}

// The list of the chats on the left. It is open on a wide screen, unless the shopper closed it
const NARROW = window.matchMedia("(max-width: 900px)");
const LOCALES = { en: "en-GB", sv: "sv-SE", zh: "zh-CN" };

function openThreads(open, keep = true) {
  document.body.classList.toggle("threads-open", open);
  $("threads-toggle").setAttribute("aria-expanded", String(open));
  if (open && NARROW.matches) openPanel(false);
  if (keep && !NARROW.matches) {
    try { localStorage.setItem("threads-open", open ? "1" : "0"); } catch (e) { /* only a convenience */ }
  }
}

function threadsWanted() {
  if (NARROW.matches) return false;
  try { return localStorage.getItem("threads-open") !== "0"; } catch (e) { return true; }
}

function when(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const today = d.toDateString() === new Date().toDateString();
  return today ? d.toLocaleTimeString(LOCALES[lang], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(LOCALES[lang], { day: "numeric", month: "short", year: "numeric" });
}

async function loadThreads() {
  const list = $("thread-list");
  let rows = null;
  try {
    const response = await fetch("/api/threads");
    if (response.ok) rows = await response.json();
  } catch (e) { /* the note below */ }
  if (!rows) {
    if (!list.querySelector("a")) list.replaceChildren(el("li", "empty-note", tr("chats_failed")));
    return;
  }
  if (!rows.length) { list.replaceChildren(el("li", "empty-note", tr("no_chats"))); return; }
  list.replaceChildren(...rows.map((row) => {
    const li = el("li");
    const link = el("a", row.thread === thread ? "current" : null);
    link.href = `#t=${row.thread}`;
    if (row.thread === thread) link.setAttribute("aria-current", "page");
    link.append(el("span", "t-title", row.title), el("span", "t-date", when(row.last)));
    link.addEventListener("click", (e) => {
      if (busy) e.preventDefault();
      else if (NARROW.matches) openThreads(false);
    });
    li.append(link);
    return li;
  }));
}

// One turn
async function sendMessage(text) {
  if (busy || !text.trim()) return;
  if (!threadFromHash()) history.replaceState(null, "", `#t=${thread}`);
  addMessage("user", text);
  input.value = ""; grow();
  setBusy(true);
  const p = progress();
  log.append(p.box);
  scrollDown();
  let started = false, ended = false;
  try {
    const response = await fetch(`/api/threads/${thread}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) {
      addMessage("error", await errorText(response));
      return;
    }
    started = true;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        ended = ended || event.type === "done";
        handle(event, p);
      }
    }
  } catch (err) {
    if (!started) addMessage("error", tr("connection", { error: err.message }));
  } finally {
    finish(p);
    $("problem").hidden = true;
    setBusy(false);
    input.focus();
    loadThreads();
  }
  if (started && !ended) waitForTurn();
}

// The stream broke (for example a phone that went to sleep), or the page opened while a turn ran. The turn goes on
// in the server, so ask for the conversation until the turn is done, then show it.
const WAIT_EVERY = 10000;  // ms

async function waitForTurn() {
  const waiting = thread;
  setBusy(true);
  log.append(el("div", "msg note", tr("still_working")));
  scrollDown();
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, WAIT_EVERY));
    if (thread !== waiting) return;  // the shopper opened another chat; load() of that chat shows it
    let response;
    try { response = await fetch(`/api/threads/${waiting}`); } catch (e) { continue; }  // still offline
    if (response.status === 409) continue;  // the turn still runs
    if (thread === waiting) load();
    return;
  }
}

function handle(event, p) {
  if (event.type === "step") {
    onStep(p, event);
  } else if (event.type === "status") {
    onStatus(p, event.text);
  } else if (event.type === "lang") {
    setLang(event.lang);
    setBusy(busy);
  } else if (event.type === "problem") {
    showProblem(event);
  } else if (event.type === "reply") {
    log.append(el("div", "msg assistant"));
    log.lastChild.append(linked(event.text));
  } else if (event.type === "error") {
    log.append(el("div", "msg error", event.text));
  } else if (event.type === "picks") {
    showPicks(event, true);
  }
  scrollDown();
}

async function load() {
  thread = threadFromHash();
  log.replaceChildren();
  showPicks({ picks: [], removed: [] }, false);
  document.body.classList.add("empty");
  setBusy(false);
  if (!thread) {
    thread = newThread();
    input.focus();
    loadThreads();
    return;
  }
  const response = await fetch(`/api/threads/${thread}`);
  loadThreads();
  if (response.status === 409) {  // a turn runs, perhaps of this chat
    waitForTurn();
    return;
  }
  if (!response.ok) {
    addMessage("error", await errorText(response, "not_loaded"));
    return;
  }
  const data = await response.json();
  setLang(data.lang);
  data.messages.forEach((m) => addMessage(m.role, m.content));
  setBusy(false);
  showPicks(data, true);
  input.focus();
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(input.value); });
input.addEventListener("input", grow);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(input.value); }
});
$("examples").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") sendMessage(e.target.textContent); });
$("new-chat").addEventListener("click", () => { if (!busy) { history.pushState(null, "", "/"); load(); } });
$("brand").addEventListener("click", (e) => { e.preventDefault(); $("new-chat").click(); });
$("picks-toggle").addEventListener("click", () => openPanel(!document.body.classList.contains("panel-open")));
$("threads-toggle").addEventListener("click", () => openThreads(!document.body.classList.contains("threads-open")));
$("threads-close").addEventListener("click", () => openThreads(false));
$("panel-close").addEventListener("click", () => openPanel(false));
// The memory of the shopper across conversations: see and delete
async function showMemory() {
  const list = $("memory-list");
  let items = null;
  try {
    const response = await fetch("/api/memory");
    if (response.ok) items = await response.json();
  } catch (e) { /* the message below */ }
  if (!items) { list.replaceChildren(el("li", "empty-note", tr("memory_failed"))); return; }
  if (!items.length) { list.replaceChildren(el("li", "empty-note", tr("nothing"))); return; }
  list.replaceChildren(...items.map((m) => {
    const li = el("li");
    const body = el("div", "m-body");
    const meta = el("div", "m-meta");
    meta.append(el("span", `m-kind ${m.kind}`, tr(m.kind)), ` · ${m.category || tr("all_categories")} · ${m.date}`);
    body.append(meta, el("div", null, m.text));
    const remove = el("button", "ghost", tr("delete"));
    remove.type = "button";
    remove.setAttribute("aria-label", tr("delete_memory", { text: m.text }));
    remove.addEventListener("click", async () => {
      const r = await fetch(`/api/memory/${m.id}`, { method: "DELETE" });
      if (r.ok) showMemory();
      else remove.textContent = tr("try_later");
    });
    li.append(body, remove);
    return li;
  }));
}

$("memory-open").addEventListener("click", () => { $("memory").showModal(); showMemory(); });
$("memory-close").addEventListener("click", () => $("memory").close());
$("problem-done").addEventListener("click", () => answerProblem(false));
$("problem-skip").addEventListener("click", () => answerProblem(true));
window.addEventListener("popstate", () => { if (!busy) load(); });
window.addEventListener("hashchange", () => { if (!busy && threadFromHash() !== thread) load(); });
setLang(lang);
setBusy(false);
openThreads(threadsWanted(), false);
load();
