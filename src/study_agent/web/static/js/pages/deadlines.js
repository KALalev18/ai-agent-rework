import { api } from "../api.js";
import { badge, confirmAction, courseTag, el, empty, hours, icon, itemActions, modal, progressBar } from "../ui.js";

// Detail drawer for one deadline or task; used from every page.
export async function openItem(id) {
  let data;
  try {
    data = await api.get(`/api/items/${encodeURIComponent(id)}`);
  } catch {
    return;
  }
  const item = data.item;
  const body = el("div", { class: "detail" },
    el("div", { class: "detail-head" }, courseTag(item.course), badge(item.risk), el("span", { class: "muted small" }, item.kind === "task" ? "My task" : `Deadline · ${item.source}`)),
    item.due_local ? el("p", { class: "detail-due" }, icon("flag", 16), `Due ${item.due_local}`) : null,
    item.risk_reason ? el("p", { class: "muted" }, item.risk_reason) : null,
    el("div", { class: "detail-progress" },
      el("div", { class: "row between" }, el("span", {}, `${hours(item.logged_min)} logged`), el("span", { class: "muted" }, `of ${hours(item.estimate_min)} (${item.estimate_source === "mine" ? "your estimate" : "default estimate"})`)),
      progressBar(item),
    ),
    itemActions(item),
    el("h3", {}, "Study blocks"),
    data.blocks.length
      ? el("ul", { class: "list" }, data.blocks.map((b) => el("li", { class: "list-row" }, el("span", { class: "when" }, b.when), el("span", { class: "grow muted" }, b.block_id),
          el("button", { class: "small ghost", title: "Remove", onclick: () => confirmAction("delete_study_block", { block_id: b.block_id }) }, icon("trash", 14)))))
      : el("p", { class: "muted small" }, "No saved blocks. Save suggestions on the Today or Planner page."),
    el("h3", {}, "Progress log"),
    data.progress.length
      ? el("ul", { class: "list" }, data.progress.map((p) => el("li", { class: "list-row" }, el("span", { class: "when" }, p.logged), el("span", { class: "grow" }, p.text), p.minutes ? el("span", { class: "muted" }, `${p.minutes} min`) : null)))
      : el("p", { class: "muted small" }, "Nothing logged yet."),
    el("p", { class: "muted tiny" }, `Id ${item.id}`),
  );
  window.addEventListener("data-changed", closeOnChange, { once: true });
  function closeOnChange() {
    document.querySelector("#modal-root .overlay")?.click();
  }
  await modal({ title: item.title, body, side: true });
}

const RANGES = [
  ["7", "Next 7 days"],
  ["14", "Next 14 days"],
  ["30", "Next 30 days"],
  ["all", "Everything open"],
];
const STATUSES = [
  ["all", "All"],
  ["attention", "Needs attention"],
  ["tight", "Tight"],
  ["ok", "On track"],
];

export async function render(view, { kind = "deadline" } = {}) {
  const data = await api.get("/api/items?include_done=true");
  const state = { q: "", course: "", range: "all", status: "all", done: false };
  const now = Date.parse(data.now); // the app's clock (frozen in demo mode), not the browser's

  const search = el("input", { type: "search", placeholder: "Search titles…", class: "search", oninput: (e) => { state.q = e.target.value.toLowerCase(); draw(); } });
  const course = el("select", { onchange: (e) => { state.course = e.target.value; draw(); } }, el("option", { value: "" }, "All courses"), data.courses.map((c) => el("option", { value: c }, c)));
  const range = el("select", { onchange: (e) => { state.range = e.target.value; draw(); } }, RANGES.map(([v, l]) => el("option", { value: v, selected: v === state.range }, l)));
  const chips = el("div", { class: "segmented" });
  const doneToggle = el("label", { class: "check" }, el("input", { type: "checkbox", onchange: (e) => { state.done = e.target.checked; draw(); } }), "Show finished");
  const results = el("div");

  function drawChips() {
    chips.replaceChildren(...STATUSES.map(([v, l]) => el("button", { class: state.status === v ? "active" : "", onclick: () => { state.status = v; drawChips(); draw(); } }, l)));
  }

  function matches(item) {
    if (item.kind !== kind) return false;
    if (state.course && item.course !== state.course) return false;
    if (state.q && !item.title.toLowerCase().includes(state.q)) return false;
    if (state.status === "attention" && !["overdue", "at_risk"].includes(item.risk)) return false;
    if (state.status === "tight" && item.risk !== "tight") return false;
    if (state.status === "ok" && !["ok", "done_enough", "no_due"].includes(item.risk)) return false;
    if (state.range !== "all" && item.due && item.risk !== "overdue") {
      const limit = now + Number(state.range) * 86400000;
      if (new Date(item.due).getTime() > limit) return false;
    }
    return true;
  }

  function draw() {
    const rows = data.items.filter(matches);
    const done = state.done ? data.done.filter((d) => d.kind === kind && (!state.course || d.course === state.course) && (!state.q || d.title.toLowerCase().includes(state.q))) : [];
    if (!rows.length && !done.length) {
      results.replaceChildren(empty("Nothing matches these filters.", "search"));
      return;
    }
    results.replaceChildren(
      el("div", { class: "table-wrap" },
        el("table", { class: "data" },
          el("thead", {}, el("tr", {}, el("th", {}, "Due"), el("th", {}, "Course"), el("th", {}, "Title"), el("th", {}, "Progress"), el("th", {}, "Left"), el("th", {}, "Status"), el("th", {}, ""))),
          el("tbody", {},
            rows.map((item) => el("tr", { class: "clickable", onclick: () => openItem(item.id) },
              el("td", { class: "nowrap" }, item.due_local || "-"),
              el("td", {}, courseTag(item.course)),
              el("td", {}, item.title),
              el("td", { class: "progress-cell" }, progressBar(item)),
              el("td", { class: "nowrap" }, hours(item.remaining_min)),
              el("td", {}, badge(item.risk)),
              el("td", { class: "row-actions" }, itemActions(item)),
            )),
            done.map((d) => el("tr", { class: "done-row" },
              el("td", { class: "nowrap" }, d.due_local || "-"),
              el("td", {}, courseTag(d.course)),
              el("td", {}, d.title),
              el("td", { colspan: "3", class: "muted" }, `Finished ${d.done_at}`),
              el("td", {}, ""),
            )),
          ),
        ),
      ),
    );
  }

  drawChips();
  draw();
  const open = data.items.filter((i) => i.kind === kind);
  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, kind === "task" ? "Tasks" : "Deadlines"),
        el("p", { class: "lead" }, kind === "task" ? "Your own to-dos, planned around your deadlines." : `${open.length} open from your MyCourses calendar, with risk computed from your free time.`)),
    ),
    el("div", { class: "toolbar" }, search, course, range, chips, doneToggle),
    el("article", { class: "card flush" }, results),
  );
  return { results, data };
}
