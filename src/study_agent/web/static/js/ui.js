// Shared UI helpers. Calendar titles and model answers are untrusted text: everything is built
// with textContent; only renderMarkdown() emits HTML, and it escapes its input first.

import { api } from "./api.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const ICONS = {
  today: "M3 12l9-9 9 9M5 10v10h14V10",
  planner: "M4 5h16v15H4zM4 10h16M9 3v4M15 3v4",
  calendar: "M4 5h16v15H4zM4 10h16M9 3v4M15 3v4M8 14h2M14 14h2M8 17h2M14 17h2",
  book: "M4 19V5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 19a2 2 0 012-2h13M9 7h6",
  deadlines: "M12 7v5l3 3M12 21a9 9 0 110-18 9 9 0 010 18z",
  tasks: "M9 11l3 3 8-8M20 12v7a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2h9",
  progress: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  assistant: "M21 12a8 8 0 01-11.6 7.1L4 20l1-4.4A8 8 0 1121 12z",
  insights: "M3 3v18h18M7 15l4-4 3 3 5-6",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
  search: "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3",
  plus: "M12 5v14M5 12h14",
  check: "M5 13l4 4L19 7",
  clock: "M12 7v5l3 2M12 21a9 9 0 110-18 9 9 0 010 18z",
  x: "M6 6l12 12M18 6L6 18",
  chevronLeft: "M15 18l-6-6 6-6",
  chevronRight: "M9 18l6-6-6-6",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  sun: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  refresh: "M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5",
  flag: "M4 22V4M4 4h13l-2 4 2 4H4",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
};

export function icon(name, size = 18) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.sparkle);
  svg.append(path);
  return svg;
}

const RISK_LABEL = { overdue: "Overdue", at_risk: "At risk", tight: "Tight", ok: "On track", done_enough: "Covered", no_due: "No due date" };

export function badge(status) {
  return el("span", { class: `badge ${status || "none"}` }, RISK_LABEL[status] || status || "-");
}

export const hours = (minutes) => `${(minutes / 60).toFixed(minutes % 60 === 0 ? 0 : 1)} h`;
export const hm = (iso) => iso.slice(11, 16);
export const isoDate = (iso) => iso.slice(0, 10);

export function courseTag(code) {
  return el("span", { class: "course", "data-course": code }, code);
}

// Colour per course. Courses get distinct palette hues in alphabetical order (registered once
// from the overview), so colours never collide for up to 12 courses and match on every page.
const PALETTE = [220, 150, 275, 190, 35, 320, 95, 250, 170, 300, 15, 350]; // red last: it means "exam" / "at risk"
const assigned = new Map();

export function registerCourses(codes) {
  assigned.clear();
  [...new Set(codes)].sort().forEach((code, i) => assigned.set(code, PALETTE[i % PALETTE.length]));
}

export function courseHue(code) {
  if (assigned.has(code)) return assigned.get(code);
  let hash = 0;
  for (const ch of code || "") hash = (hash * 31 + ch.charCodeAt(0)) % 997;
  return PALETTE[hash % PALETTE.length];
}

export function empty(text, iconName = "sparkle") {
  return el("div", { class: "empty-state" }, icon(iconName, 28), el("p", {}, text));
}

export function skeleton(lines = 3) {
  return el("div", { class: "skeleton-wrap" }, Array.from({ length: lines }, () => el("div", { class: "skeleton" })));
}

// --- toasts ------------------------------------------------------------------------------------

export function toast(message, kind = "info") {
  const root = document.getElementById("toasts");
  const node = el("div", { class: `toast ${kind}`, role: "status" }, icon(kind === "error" ? "x" : "check", 16), el("span", {}, message));
  root.append(node);
  setTimeout(() => node.classList.add("leaving"), 3600);
  setTimeout(() => node.remove(), 4000);
}

// --- modals ------------------------------------------------------------------------------------

export function modal({ title, body, actions = [], wide = false, side = false }) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    const close = (value) => {
      overlay.classList.add("leaving");
      document.removeEventListener("keydown", onKey);
      setTimeout(() => overlay.remove(), 150);
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close(null);
    };
    const footerButtons = actions.map((a) =>
      el("button", { type: a.submit ? "submit" : "button", class: a.kind || "", onclick: a.submit ? undefined : () => close(a.value) }, a.label),
    );
    const content = el("div", { class: `modal ${wide ? "wide" : ""} ${side ? "side" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
      el("div", { class: "modal-head" }, el("h2", {}, title), el("button", { class: "icon-button", "aria-label": "Close", onclick: () => close(null) }, icon("x"))),
      el("div", { class: "modal-body" }, body),
      footerButtons.length ? el("div", { class: "modal-foot" }, footerButtons) : null,
    );
    const overlay = el("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(null); } }, content);
    root.append(overlay);
    document.addEventListener("keydown", onKey);
    const form = body.querySelector?.("form") || (body.tagName === "FORM" ? body : null);
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        close(Object.fromEntries(new FormData(form)));
      });
      for (const button of footerButtons) if (button.type === "submit") button.setAttribute("form", form.id);
    }
    setTimeout(() => (content.querySelector("input, select, textarea, button.primary") || content).focus(), 30);
  });
}

// Every change from a button: ask the server for the proposal (validation runs here, so bad
// requests fail before I'm asked), show it, and only apply on "Confirm".
export async function confirmAction(name, args, { silent = false } = {}) {
  let proposal;
  try {
    proposal = await api.preview(name, args);
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
  if (!silent) {
    const ok = await modal({
      title: proposal.action,
      body: el("div", {}, el("p", { class: "muted" }, "This change will be saved:"), el("pre", { class: "proposal-lines" }, proposal.lines.join("\n"))),
      actions: [
        { label: "Cancel", kind: "ghost", value: false },
        { label: "Confirm", kind: "primary", value: true },
      ],
    });
    if (!ok) return null;
  }
  try {
    const result = await api.action(name, args);
    toast(`${proposal.action}: saved`, "success");
    window.dispatchEvent(new CustomEvent("data-changed"));
    return result;
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

// --- markdown (agent answers) -------------------------------------------------------------------

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function renderMarkdown(text) {
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
  const container = el("div", { class: "markdown" });
  container.innerHTML = out.join("");
  return container;
}

// --- shared item actions (used on several pages) ------------------------------------------------

export async function logTimeDialog(item) {
  const form = el("form", { id: "log-form", class: "form" },
    el("label", {}, "What did you do?", el("input", { name: "text", required: true, maxlength: "300", value: `Worked on ${item.title}` })),
    el("div", { class: "row" },
      el("label", {}, "Minutes", el("input", { name: "minutes", type: "number", min: "5", max: "1440", step: "5", value: "60", required: true })),
      el("label", { class: "check" }, el("input", { name: "mark_done", type: "checkbox" }), "Finished it"),
    ),
  );
  const values = await modal({ title: `Log time: ${item.course} ${item.title}`, body: form, actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: "Continue", kind: "primary", submit: true }] });
  if (!values) return;
  await confirmAction("log_progress", { text: values.text, item_id: item.id, minutes: Number(values.minutes), mark_done: values.mark_done === "on" });
}

export async function estimateDialog(item) {
  const form = el("form", { id: "estimate-form", class: "form" },
    el("p", { class: "muted" }, `Current estimate: ${hours(item.estimate_min)} (${item.estimate_source === "mine" ? "yours" : "default by type"}). Logged so far: ${hours(item.logged_min)}.`),
    el("label", {}, "Total effort in hours", el("input", { name: "hours", type: "number", min: "0.5", max: "100", step: "0.5", value: (item.estimate_min / 60).toFixed(1), required: true })),
  );
  const values = await modal({ title: `Estimate: ${item.title}`, body: form, actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: "Continue", kind: "primary", submit: true }] });
  if (!values) return;
  await confirmAction("set_estimate", { item_id: item.id, estimate_min: Math.round(Number(values.hours) * 60) });
}

export function itemActions(item) {
  return el("div", { class: "actions" },
    el("button", { class: "small ghost", title: "Log time", onclick: (e) => { e.stopPropagation(); logTimeDialog(item); } }, icon("clock", 14), "Log"),
    el("button", { class: "small ghost", title: "Change estimate", onclick: (e) => { e.stopPropagation(); estimateDialog(item); } }, icon("edit", 14), "Estimate"),
    el("button", { class: "small ghost", title: "Mark as done", onclick: (e) => { e.stopPropagation(); confirmAction("complete_item", { item_id: item.id }); } }, icon("check", 14), "Done"),
  );
}

export function progressBar(item) {
  const pct = item.estimate_min ? Math.min(100, Math.round((item.logged_min / item.estimate_min) * 100)) : 0;
  return el("div", { class: "progressbar", title: `${hours(item.logged_min)} of ${hours(item.estimate_min)} logged` }, el("div", { style: `width:${pct}%` }));
}
