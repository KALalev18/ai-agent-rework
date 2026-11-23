// Study Agent web UI. Plain JavaScript, no build step.
// Calendar titles and model answers are untrusted text: they are inserted with textContent,
// or HTML-escaped before the small Markdown renderer adds its own tags.

const $ = (id) => document.getElementById(id);
const HEADERS = { "Content-Type": "application/json", "X-Study-Agent": "1" };
const QUICK = ["Give me my morning brief for today.", "Plan my week.", "What's due this week?", "What am I at risk of missing?"];
const RISK_LABEL = { overdue: "overdue", at_risk: "at risk", tight: "tight", ok: "ok", done_enough: "covered", no_due: "no due date" };

let conversationId = null;
let busy = false;
let mode = "live";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function badge(status) {
  return el("span", { class: `badge ${status}` }, RISK_LABEL[status] || status);
}

function hm(iso) {
  return iso.slice(11, 16);
}

// --- dashboard -------------------------------------------------------------------------------

async function loadOverview() {
  const response = await fetch("/api/overview");
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || response.statusText;
    $("warnings").replaceChildren(el("div", { class: "warning" }, `Could not load calendars: ${detail}`));
    return;
  }
  const data = await response.json();
  mode = data.mode;
  $("mode").textContent = data.mode === "demo" ? "DEMO · fictional semester" : `LIVE · ${data.model}`;
  $("now").textContent = data.now;
  $("warnings").replaceChildren(...data.warnings.map((w) => el("div", { class: "warning" }, w)));
  renderRisks(data.today.at_risk);
  renderTimeline(data.today);
  renderDeadlines(data.deadlines);
  renderChips(data.mode === "demo" ? data.demo_questions : QUICK);
  if (data.mode === "demo") {
    $("question").placeholder = "Demo mode replays scripted answers: pick a question above.";
    $("question").disabled = true;
  }
  $("send").disabled = busy || data.mode === "demo";
}

function renderRisks(items) {
  if (!items.length) {
    $("risks").replaceChildren(el("li", {}, badge("ok"), "Nothing at risk."));
    return;
  }
  $("risks").replaceChildren(
    ...items.map((item) =>
      el(
        "li",
        {},
        badge(item.risk),
        el("span", {}, el("span", { class: "course" }, item.course), " ", item.title, " · due ", item.due_local),
        el("span", { class: "reason" }, item.risk_reason),
      ),
    ),
  );
}

function renderTimeline(day) {
  $("today-title").textContent = `Today · ${day.day}`;
  const rows = [
    ...day.events.map((e) => ({ start: e.start, end: e.end, cls: "event", text: e.title, tag: e.kind })),
    ...day.saved_blocks.map((b) => ({ start: b.start, end: b.end, cls: "saved", text: `${b.course} ${b.title}`, tag: `saved ${b.block_id}` })),
    ...day.suggested_blocks.map((b) => ({ start: b.start, end: b.end, cls: "suggested", text: `${b.course} ${b.title}`, tag: `suggested${b.for_due ? ", due " + b.for_due : ""}` })),
  ].sort((a, b) => a.start.localeCompare(b.start));
  if (!rows.length) {
    $("timeline").replaceChildren(el("li", {}, el("span", { class: "time" }, "-"), "Nothing scheduled and no free time left today."));
    return;
  }
  $("timeline").replaceChildren(
    ...rows.map((r) =>
      el("li", { class: r.cls }, el("span", { class: "time" }, `${hm(r.start)}-${hm(r.end)}`), el("span", {}, r.text, el("span", { class: "tag" }, r.tag))),
    ),
  );
}

function renderDeadlines(items) {
  if (!items.length) {
    $("deadlines").replaceChildren(el("tr", {}, el("td", { colspan: "5" }, "No open deadlines in the next 14 days.")));
    return;
  }
  $("deadlines").replaceChildren(
    ...items.map((d) =>
      el(
        "tr",
        { title: `Source: ${d.source} · id ${d.id}` },
        el("td", { class: "nowrap" }, d.due_local),
        el("td", { class: "course" }, d.course),
        el("td", {}, d.title),
        el("td", { class: "nowrap" }, `${(d.remaining_min / 60).toFixed(1)} h`),
        el("td", {}, badge(d.risk)),
      ),
    ),
  );
}

const CHIPS_SHOWN = 6;

function renderChips(questions, expanded = false) {
  const shown = expanded ? questions : questions.slice(0, CHIPS_SHOWN);
  const chips = shown.map((q) => el("button", { type: "button", onclick: () => ask(q) }, q));
  if (questions.length > CHIPS_SHOWN) {
    const label = expanded ? "fewer" : `+${questions.length - CHIPS_SHOWN} more`;
    chips.push(el("button", { type: "button", class: "more", onclick: () => renderChips(questions, !expanded) }, label));
  }
  $("chips").replaceChildren(...chips);
}

// --- chat ------------------------------------------------------------------------------------

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Enough Markdown for the agent's answers: headings, bold, bullet lists, paragraphs.
function renderMarkdown(text) {
  const inline = (s) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = false;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (!list) out.push("<ul>");
      list = true;
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) out.push("</ul>");
    list = false;
    if (!line) continue;
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    out.push(heading ? `<h4>${inline(heading[1])}</h4>` : `<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

function append(node) {
  const log = $("log");
  log.querySelector(".empty")?.remove();
  log.append(node);
  log.scrollTop = log.scrollHeight;
}

function argsText(input) {
  return Object.entries(input || {})
    .map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${text.length > 40 ? text.slice(0, 37) + "..." : text}`;
    })
    .join(", ");
}

function renderEvent(runId, event) {
  switch (event.kind) {
    case "tool_call":
      append(el("div", { class: "trace" }, `→ ${event.name}(${argsText(event.input)})`));
      break;
    case "tool_result":
      if (!event.ok) {
        let message = event.error;
        try { message = JSON.parse(event.error).error; } catch { /* keep raw text */ }
        append(el("div", { class: "trace warn" }, `! ${event.name} refused: ${message}`));
      }
      break;
    case "proposal":
      append(proposalCard(runId, event));
      break;
    case "decision":
      append(el("div", { class: `trace ${event.approved ? "good" : "bad"}` }, event.approved ? "✓ approved and applied" : "✗ declined, nothing saved"));
      break;
    case "grounding_retry":
      append(el("div", { class: "trace warn" }, `! grounding check: ${event.claims} not found in any tool result; asking the model to correct`));
      break;
    case "info":
      append(el("div", { class: "trace warn" }, event.text));
      break;
    case "answer": {
      const body = el("div", { class: "answer" });
      body.innerHTML = renderMarkdown(event.text || "(no answer)");
      append(body);
      if (!event.grounded) {
        append(el("div", { class: "ground-warning" }, `Not found in any tool result, treat as unverified: ${event.unsupported}`));
      }
      append(el("div", { class: "footer" }, event.footer));
      break;
    }
    case "error":
      append(el("div", { class: "error" }, `Error: ${event.message}`));
      break;
  }
}

function proposalCard(runId, proposal) {
  const card = el("div", { class: "proposal" }, el("h3", {}, `Proposed change: ${proposal.action}`), el("pre", {}, proposal.lines.join("\n")));
  if (proposal.scripted !== undefined) {
    card.append(el("div", { class: "trace" }, `Demo: scripted answer is "${proposal.scripted ? "approve" : "decline"}".`));
    return card;
  }
  const decide = async (approve) => {
    for (const b of card.querySelectorAll("button")) b.disabled = true;
    const response = await fetch(`/api/runs/${runId}/decision`, { method: "POST", headers: HEADERS, body: JSON.stringify({ approve }) });
    if (!response.ok) append(el("div", { class: "error" }, "Could not send the decision."));
  };
  card.append(
    el(
      "div",
      { class: "actions" },
      el("button", { type: "button", onclick: () => decide(true) }, "Approve"),
      el("button", { type: "button", class: "decline", onclick: () => decide(false) }, "Decline"),
    ),
  );
  return card;
}

async function ask(question) {
  if (busy || !question.trim()) return;
  setBusy(true);
  append(el("div", { class: "msg-user" }, question));
  try {
    const response = await fetch("/api/ask", { method: "POST", headers: HEADERS, body: JSON.stringify({ question, conversation_id: conversationId }) });
    if (!response.ok) throw new Error(`request failed (${response.status})`);
    const { run_id: runId } = await response.json();
    if (mode === "live") conversationId = conversationId || runId;
    await follow(runId);
  } catch (error) {
    append(el("div", { class: "error" }, `Error: ${error.message}`));
  } finally {
    setBusy(false);
    loadOverview(); // an approved change may have moved blocks or finished deadlines
  }
}

async function follow(runId) {
  let next = 0;
  const thinking = el("div", { class: "trace" }, "thinking…");
  append(thinking);
  for (;;) {
    const response = await fetch(`/api/runs/${runId}?after=${next}`);
    if (!response.ok) throw new Error(`lost the run (${response.status})`);
    const data = await response.json();
    for (const event of data.events) {
      thinking.remove();
      renderEvent(runId, event);
      append(thinking);
    }
    next = data.next;
    thinking.textContent = data.status === "waiting_approval" ? "waiting for your approval…" : "thinking…";
    if (data.status === "done" || data.status === "error") break;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  thinking.remove();
}

function setBusy(value) {
  busy = value;
  $("send").disabled = value || mode === "demo";
  for (const b of $("chips").querySelectorAll("button")) b.disabled = value;
}

$("ask").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("question").value;
  $("question").value = "";
  ask(question);
});
$("new-chat").addEventListener("click", () => {
  conversationId = null;
  $("log").replaceChildren(el("p", { class: "empty" }, "New conversation."));
});
$("refresh").addEventListener("click", loadOverview);
loadOverview();
