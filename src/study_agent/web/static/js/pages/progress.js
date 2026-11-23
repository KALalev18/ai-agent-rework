import { api } from "../api.js";
import { confirmAction, courseHue, el, empty, hours, icon } from "../ui.js";

function barChart(rows) {
  const max = Math.max(60, ...rows.map((r) => r.minutes));
  return el("div", { class: "bars" }, rows.map((r) =>
    el("div", { class: "bar-col", title: `${r.label}: ${hours(r.minutes)}` },
      el("div", { class: "bar-track" }, el("div", { class: "bar", style: `height:${(r.minutes / max) * 100}%` })),
      el("span", { class: "bar-label" }, r.label.split(" ")[1]),
    )));
}

function courseSplit(perCourse) {
  const total = Object.values(perCourse).reduce((a, b) => a + b, 0);
  if (!total) return empty("No time logged in this period yet.", "progress");
  return el("div", { class: "split" },
    el("div", { class: "split-bar" }, Object.entries(perCourse).map(([course, m]) =>
      el("span", { style: `width:${(m / total) * 100}%;--hue:${courseHue(course)}`, title: `${course}: ${hours(m)}` }))),
    el("ul", { class: "split-legend" }, Object.entries(perCourse).map(([course, m]) =>
      el("li", {}, el("i", { style: `--hue:${courseHue(course)}` }), el("span", { class: "course" }, course), el("strong", {}, hours(m)), el("span", { class: "muted" }, `${Math.round((m / total) * 100)}%`)))),
  );
}

export async function render(view) {
  const [data, items] = await Promise.all([api.get("/api/progress?days=14"), api.get("/api/items")]);
  const total = data.per_day.reduce((a, d) => a + d.minutes, 0);
  const activeDays = data.per_day.filter((d) => d.minutes > 0).length;

  const form = el("form", { class: "form log-form" },
    el("label", { class: "grow" }, "What did you work on?", el("input", { name: "text", required: true, maxlength: "300", placeholder: "e.g. Finished the proofs for exercise round 8" })),
    el("label", {}, "For", el("select", { name: "item_id" }, el("option", { value: "" }, "General (no item)"),
      items.items.map((i) => el("option", { value: i.id }, `${i.course} · ${i.title}`)))),
    el("label", { class: "narrow" }, "Minutes", el("input", { name: "minutes", type: "number", min: "5", max: "1440", step: "5", value: "60" })),
    el("label", { class: "check" }, el("input", { name: "mark_done", type: "checkbox" }), "Finished"),
    el("button", { type: "submit", class: "primary" }, icon("plus", 16), "Log"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const v = Object.fromEntries(new FormData(form));
    const args = { text: v.text.trim() };
    if (v.item_id) args.item_id = v.item_id;
    if (v.minutes) args.minutes = Number(v.minutes);
    if (v.mark_done === "on" && v.item_id) args.mark_done = true;
    if (await confirmAction("log_progress", args)) form.reset();
  });

  const byDay = {};
  for (const p of data.progress) (byDay[p.logged.split(" ").slice(0, 3).join(" ")] ||= []).push(p);

  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Progress"), el("p", { class: "lead" }, "Logged time lowers the remaining effort, so the risk check stays honest.")),
    ),
    el("article", { class: "card" }, form),
    el("section", { class: "stats three" },
      el("div", { class: "stat" }, el("span", { class: "stat-label" }, "Logged, 14 days"), el("strong", {}, hours(total)), el("span", { class: "stat-hint" }, `${hours(Math.round(total / 14))} per day on average`)),
      el("div", { class: "stat" }, el("span", { class: "stat-label" }, "Active days"), el("strong", {}, `${activeDays} / 14`), el("span", { class: "stat-hint" }, "days with logged work")),
      el("div", { class: "stat" }, el("span", { class: "stat-label" }, "Finished"), el("strong", {}, String(data.tasks_completed.length)), el("span", { class: "stat-hint" }, "tasks in the last 14 days")),
    ),
    el("div", { class: "grid-2" },
      el("article", { class: "card" }, el("h2", {}, "Time per day"), barChart(data.per_day)),
      el("article", { class: "card" }, el("h2", {}, "Time per course"), courseSplit(data.per_course)),
    ),
    el("article", { class: "card" }, el("h2", {}, "History"),
      Object.keys(byDay).length
        ? el("div", { class: "history" }, Object.entries(byDay).reverse().map(([day, entries]) =>
            el("div", { class: "history-day" }, el("h3", {}, day), el("ul", { class: "list" }, entries.reverse().map((p) =>
              el("li", { class: "list-row" }, el("span", { class: "when" }, p.logged.split(" ")[3]), p.course ? el("span", { class: "course" }, p.course) : null,
                el("span", { class: "grow" }, p.text), p.minutes ? el("span", { class: "muted" }, `${p.minutes} min`) : null))))))
        : empty("Nothing logged in the last 14 days. Use the form above or tell the assistant what you did.", "progress"),
    ),
  );
}
