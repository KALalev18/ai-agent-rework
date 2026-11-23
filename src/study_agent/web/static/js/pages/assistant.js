import { api } from "../api.js";
import { el, icon, renderMarkdown } from "../ui.js";

// The conversation survives page changes (module state), like a real chat product.
const state = { log: null, conversationId: null, busy: false, mode: "live", questions: [] };
const QUICK = [
  "Give me my morning brief for today.",
  "Plan my week.",
  "What's due this week?",
  "What am I at risk of missing?",
  "What did I do yesterday?",
];

function argsText(input) {
  return Object.entries(input || {}).map(([k, v]) => {
    const text = typeof v === "string" ? v : JSON.stringify(v);
    return `${k}=${text.length > 40 ? `${text.slice(0, 37)}...` : text}`;
  }).join(", ");
}

function append(node) {
  state.log.querySelector(".chat-empty")?.remove();
  state.log.append(node);
  state.log.scrollTop = state.log.scrollHeight;
}

function proposalCard(runId, event) {
  const card = el("div", { class: "proposal" }, el("div", { class: "proposal-head" }, icon("edit", 16), el("strong", {}, `Proposed change: ${event.action}`)),
    el("pre", { class: "proposal-lines" }, event.lines.join("\n")));
  if (event.scripted !== undefined) {
    card.append(el("p", { class: "muted small" }, `Demo: the scripted answer is "${event.scripted ? "approve" : "decline"}".`));
    return card;
  }
  const decide = async (approve) => {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await api.post(`/api/runs/${runId}/decision`, { approve });
    } catch (error) {
      append(el("div", { class: "chat-error" }, error.message));
    }
  };
  card.append(el("div", { class: "actions" },
    el("button", { class: "primary small", onclick: () => decide(true) }, icon("check", 14), "Approve"),
    el("button", { class: "danger small", onclick: () => decide(false) }, icon("x", 14), "Decline")));
  return card;
}

function renderEvent(runId, event) {
  switch (event.kind) {
    case "tool_call":
      append(el("div", { class: "trace" }, icon("sparkle", 12), `${event.name}(${argsText(event.input)})`));
      break;
    case "tool_result":
      if (!event.ok) {
        let message = event.error;
        try { message = JSON.parse(event.error).error; } catch { /* keep raw */ }
        append(el("div", { class: "trace warn" }, `${event.name} refused: ${message}`));
      }
      break;
    case "proposal":
      append(proposalCard(runId, event));
      break;
    case "decision":
      append(el("div", { class: `trace ${event.approved ? "good" : "bad"}` }, event.approved ? "Approved and saved" : "Declined, nothing saved"));
      if (event.approved) window.dispatchEvent(new CustomEvent("data-changed", { detail: { quiet: true } }));
      break;
    case "grounding_retry":
      append(el("div", { class: "trace warn" }, `Grounding check: ${event.claims} not found in any tool result. Asking the model to correct.`));
      break;
    case "info":
      append(el("div", { class: "trace warn" }, event.text));
      break;
    case "answer":
      append(el("div", { class: "bubble agent" }, renderMarkdown(event.text || "(no answer)"),
        event.grounded ? null : el("div", { class: "ground-warning" }, `Not found in any tool result, treat as unverified: ${event.unsupported}`),
        el("div", { class: "answer-meta" }, el("span", { class: `pill ${event.grounded ? "good" : "danger"}` }, event.grounded ? (event.corrected ? "Grounded after correction" : "Grounded") : "Not grounded"), el("span", {}, event.footer))));
      break;
    case "error":
      append(el("div", { class: "chat-error" }, event.message));
      break;
  }
}

async function follow(runId) {
  let next = 0;
  const typing = el("div", { class: "typing" }, el("span"), el("span"), el("span"));
  append(typing);
  for (;;) {
    const data = await api.get(`/api/runs/${runId}?after=${next}`);
    for (const event of data.events) {
      typing.remove();
      renderEvent(runId, event);
      append(typing);
    }
    next = data.next;
    typing.classList.toggle("waiting", data.status === "waiting_approval");
    if (data.status === "done" || data.status === "error") break;
    await new Promise((r) => setTimeout(r, 400));
  }
  typing.remove();
}

export async function ask(question) {
  if (state.busy || !question.trim()) return;
  state.busy = true;
  document.querySelectorAll(".chat-input button, .chips button").forEach((b) => (b.disabled = true));
  append(el("div", { class: "bubble user" }, question));
  try {
    const { run_id: runId } = await api.post("/api/ask", { question, conversation_id: state.conversationId });
    if (state.mode === "live") state.conversationId ||= runId;
    await follow(runId);
  } catch (error) {
    append(el("div", { class: "chat-error" }, error.message));
  } finally {
    state.busy = false;
    document.querySelectorAll(".chat-input button, .chips button").forEach((b) => (b.disabled = false));
    if (state.mode === "demo") document.querySelector(".chat-input button[type=submit]")?.setAttribute("disabled", "");
  }
}

export async function render(view, { params = {}, overview }) {
  state.mode = overview.mode;
  state.questions = overview.mode === "demo" ? overview.demo_questions : QUICK;
  if (!state.log) {
    state.log = el("div", { class: "chat-log", "aria-live": "polite" },
      el("div", { class: "chat-empty" }, icon("assistant", 32), el("h2", {}, "Ask about your studies"),
        el("p", { class: "muted" }, "The assistant reads your calendars and tasks through tools, cites every deadline, and asks before it changes anything.")));
  }
  const input = el("input", { name: "q", autocomplete: "off", maxlength: "2000", placeholder: state.mode === "demo" ? "Demo mode replays scripted answers: pick a question above." : "Ask anything: what's due, what to work on, or tell it what you finished…", disabled: state.mode === "demo" });
  const form = el("form", { class: "chat-input" }, input, el("button", { type: "submit", class: "primary", disabled: state.mode === "demo" }, icon("chevronRight", 16), "Send"));
  form.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value; input.value = ""; ask(q); });

  const chips = el("div", { class: "chips" });
  const drawChips = (all) => {
    const list = all ? state.questions : state.questions.slice(0, 6);
    chips.replaceChildren(...list.map((q) => el("button", { onclick: () => ask(q) }, q)),
      state.questions.length > 6 ? el("button", { class: "more", onclick: () => drawChips(!all) }, all ? "Fewer" : `+${state.questions.length - 6} more`) : null);
  };
  drawChips(false);

  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Assistant"), el("p", { class: "lead" }, state.mode === "demo" ? "Demo: answers are replays of scripted model turns; the tools, approval gate and grounding check run for real." : `Claude (${overview.model}) with 13 tools. Changes need your approval.`)),
      el("button", { class: "ghost", onclick: () => { state.log = null; state.conversationId = null; render(view, { params: {}, overview }); } }, icon("plus", 16), "New chat"),
    ),
    el("article", { class: "card chat-card" }, chips, state.log, form),
  );
  if (params.ask) ask(params.ask);
  else input.focus();
}
