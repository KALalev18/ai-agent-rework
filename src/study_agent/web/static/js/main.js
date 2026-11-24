// App shell: hash router, sidebar, command palette (Ctrl+K), theme, and refresh-after-change.

import { api } from "./api.js";
import { el, icon, modal, registerCourses, skeleton, toast } from "./ui.js";
import { applyTheme, getTheme, setTheme } from "./theme.js";
import * as today from "./pages/today.js";
import * as calendar from "./pages/calendar.js";
import * as courses from "./pages/courses.js";
import * as planner from "./pages/planner.js";
import * as deadlines from "./pages/deadlines.js";
import * as tasks from "./pages/tasks.js";
import * as progress from "./pages/progress.js";
import * as assistant from "./pages/assistant.js";
import * as insights from "./pages/insights.js";
import * as settings from "./pages/settings.js";

const PAGES = [
  { id: "today", label: "Today", icon: "today", module: today, key: "1" },
  { id: "calendar", label: "Calendar", icon: "calendar", module: calendar, key: "2" },
  { id: "planner", label: "Planner", icon: "planner", module: planner, key: "3" },
  { id: "deadlines", label: "Deadlines", icon: "deadlines", module: deadlines, key: "4" },
  { id: "tasks", label: "Tasks", icon: "tasks", module: tasks, key: "5" },
  { id: "progress", label: "Progress", icon: "progress", module: progress, key: "6" },
  { id: "courses", label: "Courses", icon: "book", module: courses, key: "7" },
  { id: "assistant", label: "Assistant", icon: "assistant", module: assistant, key: "8" },
  { id: "insights", label: "Insights", icon: "insights", module: insights, key: "9" },
  { id: "settings", label: "Settings", icon: "settings", module: settings, key: "0" },
];

let overview = null;
let pendingParams = {};
const view = document.getElementById("view");

export function navigate(page, params = {}) {
  pendingParams = params;
  if (location.hash === `#/${page}`) route();
  else location.hash = `#/${page}`;
}

async function refreshOverview() {
  try {
    overview = await api.get("/api/overview");
    registerCourses(overview.courses || []);
  } catch (error) {
    overview = overview || { mode: "live", model: "", now: "", demo_questions: [], stats: {} };
    toast(`Could not load your calendars: ${error.message}`, "error");
  }
  document.getElementById("mode").replaceChildren(
    el("span", { class: `dot ${overview.mode}` }),
    overview.mode === "demo" ? "Demo · fictional semester" : `Live · ${overview.model}`,
  );
  document.getElementById("now").textContent = overview.now || "";
  const risky = overview.stats?.at_risk || 0;
  const badgeEl = document.querySelector('[data-page="today"] .nav-count');
  badgeEl.textContent = risky ? String(risky) : "";
  badgeEl.hidden = !risky;
}

async function route() {
  const id = (location.hash.match(/^#\/(\w+)/) || [])[1] || "today";
  const page = PAGES.find((p) => p.id === id) || PAGES[0];
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page.id));
  document.title = `${page.label} · Study Agent`;
  document.getElementById("page-title").textContent = page.label;
  document.body.classList.remove("nav-open");
  const params = pendingParams;
  pendingParams = {};
  view.replaceChildren(skeleton(4));
  if (!overview) await refreshOverview();
  try {
    await page.module.render(view, { navigate, params, overview });
  } catch (error) {
    view.replaceChildren(el("div", { class: "card error-card" }, el("h2", {}, "Something went wrong"), el("p", {}, error.message),
      el("button", { class: "primary", onclick: route }, "Try again")));
  }
  view.focus({ preventScroll: true });
}

// After any change (a button or an approved agent action), refresh the numbers and the page.
window.addEventListener("data-changed", async () => {
  await refreshOverview();
  const id = (location.hash.match(/^#\/(\w+)/) || [])[1] || "today";
  if (id !== "assistant") route();
});

// --- command palette -------------------------------------------------------------------------

function openPalette() {
  const input = el("input", { class: "palette-input", placeholder: "Jump to a page, or ask the assistant…", autocomplete: "off" });
  const list = el("ul", { class: "palette-list" });
  let selected = 0;
  let entries = [];
  const actions = () => [
    ...PAGES.map((p) => ({ label: `Go to ${p.label}`, hint: `Alt+${p.key}`, icon: p.icon, run: () => navigate(p.id) })),
    { label: "Morning brief", hint: "assistant", icon: "sparkle", run: () => navigate("assistant", { ask: "Give me my morning brief for today." }) },
    { label: "Toggle dark mode", hint: "theme", icon: "moon", run: () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark") },
  ];
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    entries = actions().filter((a) => !q || a.label.toLowerCase().includes(q));
    if (q && overview?.mode !== "demo") entries.push({ label: `Ask: "${input.value.trim()}"`, hint: "assistant", icon: "assistant", run: () => navigate("assistant", { ask: input.value.trim() }) });
    selected = Math.min(selected, Math.max(0, entries.length - 1));
    list.replaceChildren(...entries.map((a, i) => el("li", { class: i === selected ? "selected" : "", onclick: () => choose(i) }, icon(a.icon, 16), el("span", { class: "grow" }, a.label), el("kbd", {}, a.hint))));
  };
  let close;
  const choose = (i) => {
    const entry = entries[i];
    close();
    entry?.run();
  };
  input.addEventListener("input", () => { selected = 0; draw(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { selected = (selected + 1) % entries.length; draw(); e.preventDefault(); }
    if (e.key === "ArrowUp") { selected = (selected - 1 + entries.length) % entries.length; draw(); e.preventDefault(); }
    if (e.key === "Enter") { choose(selected); e.preventDefault(); }
  });
  draw();
  const body = el("div", { class: "palette" }, input, list);
  const pending = modal({ title: "Command palette", body });
  close = () => document.querySelector("#modal-root .overlay:last-child")?.click();
  return pending;
}

// --- shell -----------------------------------------------------------------------------------

function buildNav() {
  const nav = document.querySelector(".nav");
  nav.replaceChildren(...PAGES.map((p) =>
    el("a", { href: `#/${p.id}`, "data-page": p.id, title: `${p.label} (Alt+${p.key})` }, icon(p.icon), el("span", {}, p.label),
      p.id === "today" ? el("span", { class: "nav-count", hidden: true }) : null)));
}

document.addEventListener("keydown", (event) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (!document.querySelector(".palette")) openPalette();
  } else if (event.altKey && !typing) {
    const page = PAGES.find((p) => p.key === event.key);
    if (page) { event.preventDefault(); navigate(page.id); }
  }
});

document.getElementById("palette-button").addEventListener("click", openPalette);
document.getElementById("menu-button").addEventListener("click", () => document.body.classList.toggle("nav-open"));
document.getElementById("theme-button").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  setTheme(dark ? "light" : "dark");
});
document.getElementById("ask-button").addEventListener("click", () => navigate("assistant"));

applyTheme(getTheme());
buildNav();
window.addEventListener("hashchange", route);
route();

// Keep the clock and numbers fresh if the tab stays open.
setInterval(refreshOverview, 5 * 60 * 1000);

// Small first-run hint.
try {
  if (!localStorage.getItem("study-seen-hint")) {
    localStorage.setItem("study-seen-hint", "1");
    setTimeout(() => toast("Tip: press Ctrl+K to jump anywhere or ask the assistant."), 1200);
  }
} catch { /* storage unavailable */ }
export { PAGES };
