// The web chat page: one thread in the URL, the events of a turn as lines of JSON, and the picks panel.
const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), send = $("send"), cards = $("cards");
const firstHint = input.placeholder;
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
  input.placeholder = on ? "Wait for the reply"
    : document.body.classList.contains("empty") ? firstHint : "Ask about the picks, or say what you want";
}

function grow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
  send.disabled = busy || !input.value.trim();
}

// Picks panel
function openPanel(open) {
  document.body.classList.toggle("panel-open", open && picks.length > 0);
}

function chip(attr) {
  const node = el("span", attr.doubtful ? "chip doubtful" : "chip", `${attr.name} ${attr.text}`);
  if (attr.doubtful) node.title = "The size on the page does not agree with the other sizes or the volume";
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
    thumb.append(el("span", null, "no image"));
  }
  const body = el("div", "card-body");
  const title = el("a", "card-title");
  title.href = pick.url; title.target = "_blank"; title.rel = "noopener";
  title.append(el("span", "num", String(pick.number)), pick.title);
  title.title = pick.title;
  const facts = el("div", "facts");
  facts.append(el("span", "price", pick.price || "price unknown"));
  facts.append(el("span", null, pick.rating ? `★ ${pick.rating} (${pick.review_count ?? 0})` : "no ratings"));
  if (pick.in_stock === false) facts.append(el("span", null, "sold out"));
  body.append(title, facts);
  if (pick.attributes.length) {
    const chips = el("div", "chips");
    pick.attributes.forEach((a) => chips.append(chip(a)));
    body.append(chips);
  }
  if (pick.note) body.append(el("div", "card-note", pick.note));
  const del = el("button", "icon delete");
  del.type = "button";
  del.setAttribute("aria-label", `Delete pick ${pick.number}`);
  del.title = "Delete this pick";
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
    addMessage("error", (await response.json()).detail || "The pick could not be deleted.");
    return;
  }
  const data = await response.json();
  addMessage("assistant", data.message);
  showPicks(data, false);
}

// Login or captcha pause
function showProblem(event) {
  $("problem-detail").textContent = `amazon.se needs you: ${event.detail || "a check"}`;
  $("problem-url").href = event.url;
  $("problem").hidden = false;
}

async function answerProblem(skip) {
  $("problem").hidden = true;
  await fetch("/api/problem", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skip }) });
}

// One turn
async function sendMessage(text) {
  if (busy || !text.trim()) return;
  if (!threadFromHash()) history.replaceState(null, "", `#t=${thread}`);
  addMessage("user", text);
  input.value = ""; grow();
  setBusy(true);
  const status = el("div", "status");
  const working = el("div", "working");
  working.append(el("span", "dots"), "Working");
  working.firstChild.append(el("i"), el("i"), el("i"));
  log.append(status, working);
  scrollDown();
  try {
    const response = await fetch(`/api/threads/${thread}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) {
      addMessage("error", (await response.json()).detail || `The server answered ${response.status}.`);
      return;
    }
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
        if (line) handle(JSON.parse(line), status, working);
      }
    }
  } catch (err) {
    addMessage("error", `The connection to the server failed (${err.message}). Is scout web still running?`);
  } finally {
    working.remove();
    if (!status.childElementCount) status.remove();
    $("problem").hidden = true;
    setBusy(false);
    input.focus();
  }
}

function handle(event, status, working) {
  if (event.type === "status") {
    status.append(el("div", "line", event.text));
  } else if (event.type === "problem") {
    showProblem(event);
  } else if (event.type === "reply") {
    log.insertBefore(el("div", "msg assistant"), working).append(linked(event.text));
  } else if (event.type === "error") {
    log.insertBefore(el("div", "msg error", event.text), working);
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
    return;
  }
  const response = await fetch(`/api/threads/${thread}`);
  if (!response.ok) {
    addMessage("error", (await response.json()).detail || "The conversation could not be loaded.");
    return;
  }
  const data = await response.json();
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
$("panel-close").addEventListener("click", () => openPanel(false));
$("problem-done").addEventListener("click", () => answerProblem(false));
$("problem-skip").addEventListener("click", () => answerProblem(true));
window.addEventListener("popstate", () => { if (!busy) load(); });
window.addEventListener("hashchange", () => { if (!busy && threadFromHash() !== thread) load(); });
setBusy(false);
load();
