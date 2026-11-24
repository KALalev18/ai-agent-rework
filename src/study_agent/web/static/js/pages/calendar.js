import { api } from "../api.js";
import { courseHue, el, hm, icon, isoDate, modal } from "../ui.js";
import { addCourseDialog, deadlineDialog } from "./courses.js";
import { openItem } from "./deadlines.js";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAX_PER_DAY = 5;
const LAYERS = [
  ["exams", "Exams"],
  ["deadlines", "Deadlines"],
  ["classes", "Classes"],
  ["blocks", "Study blocks"],
];

const state = { month: null, layers: new Set(["exams", "deadlines", "classes", "blocks"]), hidden: new Set() };

function shiftMonth(month, n) {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

function eachDay(start, end) {
  const days = [];
  for (let d = new Date(`${start}T12:00:00Z`); d.toISOString().slice(0, 10) <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

// Everything on one day, in display order: exams, deadlines, study blocks, classes.
function entriesFor(day, data) {
  const visible = (course) => !state.hidden.has(course);
  const deadlines = data.deadlines.filter((d) => isoDate(d.due) === day && visible(d.course));
  const out = [];
  if (state.layers.has("exams")) out.push(...deadlines.filter((d) => d.category === "exam").map((d) => ({ type: "exam", d })));
  if (state.layers.has("deadlines")) out.push(...deadlines.filter((d) => d.category !== "exam").map((d) => ({ type: "deadline", d })));
  if (state.layers.has("blocks")) out.push(...data.blocks.filter((b) => isoDate(b.start) === day && visible(b.course)).map((b) => ({ type: "block", d: b })));
  if (state.layers.has("classes")) out.push(...data.events.filter((e) => isoDate(e.start) === day && visible(e.course)).map((e) => ({ type: "class", d: e })));
  return out;
}

function chip(entry, compact = true) {
  const { type, d } = entry;
  const hue = courseHue(d.course);
  if (type === "exam" || type === "deadline") {
    return el("button", {
      class: `cal-chip ${type} ${d.done ? "done" : ""} ${d.risk || ""}`, style: `--hue:${hue}`,
      title: `${d.course} ${d.title} · due ${d.due_local}${d.done ? " · done" : ""} · ${d.source}`,
      onclick: (e) => { e.stopPropagation(); if (!d.done) openItem(d.id); },
    },
    type === "exam" ? icon("flag", 12) : null,
    el("span", { class: "chip-time" }, hm(d.due)),
    el("span", { class: "chip-text" }, compact ? `${d.course} ${d.title}` : `${d.course} · ${d.title}`));
  }
  if (type === "block") {
    return el("div", { class: "cal-chip study", style: `--hue:${hue}`, title: `Study block ${d.block_id}: ${d.course} ${d.title} ${d.when}` },
      el("span", { class: "chip-time" }, hm(d.start)), el("span", { class: "chip-text" }, `Study: ${d.title}`));
  }
  return el("div", { class: "cal-chip class", style: `--hue:${hue}`, title: `${d.title} ${d.when}` },
    el("span", { class: "chip-time" }, hm(d.start)), el("span", { class: "chip-text" }, `${d.course} ${d.kind}`));
}

async function openDay(day, data, courses) {
  const entries = entriesFor(day, { ...data, deadlines: data.deadlines });
  const label = new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
  const body = el("div", { class: "day-detail" },
    entries.length
      ? el("ul", { class: "day-list" }, entries.map((entry) => el("li", {}, chip(entry, false))))
      : el("p", { class: "muted" }, "Nothing on this day."),
    el("button", { class: "primary", onclick: async () => {
      document.querySelector("#modal-root .overlay:last-child")?.click();
      await deadlineDialog({ courses: courses.courses, kinds: courses.kinds, date: day });
    } }, icon("plus", 16), "Add a deadline on this day"),
  );
  await modal({ title: label, body, side: true });
}

export async function render(view) {
  const [data, courses] = await Promise.all([api.get(`/api/calendar${state.month ? `?month=${state.month}` : ""}`), api.get("/api/courses")]);
  state.month = data.month;
  const reload = () => render(view);
  const days = eachDay(data.grid_start, data.grid_end);

  const cells = days.map((day) => {
    const entries = entriesFor(day, data);
    const inMonth = day.slice(0, 7) === data.month;
    const shown = entries.slice(0, MAX_PER_DAY);
    const hasExam = entries.some((e) => e.type === "exam");
    return el("div", {
      class: `cal-cell ${inMonth ? "" : "outside"} ${day === data.today ? "today" : ""} ${hasExam ? "has-exam" : ""} ${day < data.today ? "past" : ""}`,
      tabindex: "0", role: "button", "aria-label": `${day}: ${entries.length} item(s)`,
      onclick: () => openDay(day, data, courses),
      onkeydown: (e) => { if (e.key === "Enter") openDay(day, data, courses); },
    },
    el("div", { class: "cal-date" }, el("span", { class: "cal-num" }, String(Number(day.slice(8)))), day.slice(8) === "01" ? el("span", { class: "cal-month" }, new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" })) : null),
    el("div", { class: "cal-chips" }, shown.map((entry) => chip(entry))),
    entries.length > MAX_PER_DAY ? el("span", { class: "cal-more" }, `+${entries.length - MAX_PER_DAY} more`) : null,
    );
  });

  const monthDeadlines = data.deadlines.filter((d) => d.due.slice(0, 7) === data.month);
  const exams = monthDeadlines.filter((d) => d.category === "exam").length;
  const open = monthDeadlines.filter((d) => !d.done).length;

  const layerToggles = el("div", { class: "segmented multi" }, LAYERS.map(([key, label]) =>
    el("button", { class: state.layers.has(key) ? "active" : "", "aria-pressed": String(state.layers.has(key)), onclick: () => { state.layers.has(key) ? state.layers.delete(key) : state.layers.add(key); reload(); } }, label)));
  const courseToggles = el("div", { class: "course-toggles" }, data.courses.map((code) =>
    el("button", { class: `course-toggle ${state.hidden.has(code) ? "off" : ""}`, style: `--hue:${courseHue(code)}`, "aria-pressed": String(!state.hidden.has(code)),
      onclick: () => { state.hidden.has(code) ? state.hidden.delete(code) : state.hidden.add(code); reload(); } }, el("i"), code)));

  const noData = !data.deadlines.length && !data.events.length && !courses.courses.length;
  const sections = [
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, data.label), el("p", { class: "lead" }, `${open} open deadline${open === 1 ? "" : "s"} and ${exams} exam${exams === 1 ? "" : "s"} this month. Click a day for details or to add something.`)),
      el("div", { class: "week-nav" },
        el("button", { class: "icon-button", "aria-label": "Previous month", onclick: () => { state.month = shiftMonth(state.month, -1); reload(); } }, icon("chevronLeft")),
        el("button", { class: "ghost small", onclick: () => { state.month = null; reload(); } }, "Today"),
        el("button", { class: "icon-button", "aria-label": "Next month", onclick: () => { state.month = shiftMonth(state.month, 1); reload(); } }, icon("chevronRight")),
        el("button", { class: "primary", onclick: () => deadlineDialog({ courses: courses.courses, kinds: courses.kinds, date: data.today }) }, icon("plus", 16), "Add deadline"),
      ),
    ),
    noData
      ? el("article", { class: "card onboarding compact" }, el("p", {}, "Your calendar is empty. Add your courses with their deadlines and weekly classes, and they appear here."),
          el("button", { class: "primary", onclick: async () => { if (await addCourseDialog()) location.hash = "#/courses"; } }, icon("plus", 16), "Add a course"))
      : null,
    el("div", { class: "toolbar" }, layerToggles, courseToggles),
    el("article", { class: "card flush month-card" },
      el("div", { class: "cal-head" }, WEEKDAYS.map((d) => el("div", {}, d))),
      el("div", { class: "cal-grid" }, cells),
    ),
  ];
  view.replaceChildren(...sections.filter(Boolean));
}
