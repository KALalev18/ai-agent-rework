import { api } from "../api.js";
import { badge, confirmAction, courseTag, el, empty, hm, hours, icon, itemActions, progressBar, toast } from "../ui.js";
import { openItem } from "./deadlines.js";

function greeting(nowIso) {
  const hour = Number(nowIso.slice(11, 13));
  return hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function stat(label, value, hint, tone = "") {
  return el("div", { class: `stat ${tone}` }, el("span", { class: "stat-label" }, label), el("strong", {}, value), el("span", { class: "stat-hint" }, hint));
}

async function saveBlocks(blocks) {
  const payload = blocks.map((b) => ({ item_id: b.item_id, start: b.start.slice(0, 16), minutes: b.minutes }));
  await confirmAction("schedule_blocks", { blocks: payload });
}

function timeline(day) {
  const rows = [
    ...day.events.map((e) => ({ ...e, type: "event" })),
    ...day.saved_blocks.map((b) => ({ ...b, type: "saved" })),
    ...day.suggested_blocks.map((b) => ({ ...b, type: "suggested" })),
  ].sort((a, b) => a.start.localeCompare(b.start));
  if (!rows.length) return empty("Nothing scheduled and no free study time left today.", "planner");
  return el("ol", { class: "timeline" }, rows.map((r) => {
    const label = r.type === "event" ? r.title : `${r.course} · ${r.title}`;
    const meta = r.type === "event" ? r.kind : r.type === "saved" ? `Saved ${r.block_id}` : `Suggested${r.for_due ? ` · due ${r.for_due}` : ""}`;
    const action = r.type === "suggested"
      ? el("button", { class: "small", onclick: () => saveBlocks([r]) }, icon("plus", 14), "Save")
      : r.type === "saved"
        ? el("button", { class: "small ghost", title: "Remove block", onclick: () => confirmAction("delete_study_block", { block_id: r.block_id }) }, icon("trash", 14))
        : null;
    return el("li", { class: `slot ${r.type}` },
      el("span", { class: "time" }, `${hm(r.start)}–${hm(r.end)}`),
      el("div", { class: "slot-body" }, el("span", { class: "slot-title" }, label), el("span", { class: "slot-meta" }, meta)),
      action,
    );
  }));
}

function attention(items) {
  if (!items.length) return empty("Nothing at risk. Nice.", "check");
  return el("ul", { class: "attention" }, items.map((item) =>
    el("li", { onclick: () => openItem(item.id) },
      el("div", { class: "attention-head" }, badge(item.risk), courseTag(item.course), el("strong", {}, item.title)),
      el("p", { class: "muted small" }, `Due ${item.due_local} · ${hours(item.remaining_min)} left · ${item.risk_reason}`),
      progressBar(item),
      itemActions(item),
    ),
  ));
}

function comingUp(items) {
  if (!items.length) return empty("No deadlines in the next 7 days.", "deadlines");
  return el("ul", { class: "list" }, items.map((item) =>
    el("li", { class: "list-row", onclick: () => openItem(item.id) },
      el("span", { class: "when" }, item.due_local),
      courseTag(item.course),
      el("span", { class: "grow" }, item.title),
      badge(item.risk),
    ),
  ));
}

export async function render(view, { navigate }) {
  const data = await api.get("/api/overview");
  const s = data.stats;
  const day = data.today;
  const suggested = day.suggested_blocks;
  const summary = [
    `${s.due_this_week} deadline${s.due_this_week === 1 ? "" : "s"} this week`,
    s.at_risk ? `${s.at_risk} need${s.at_risk === 1 ? "s" : ""} attention` : "nothing at risk",
    `${hours(s.planned_today_min)} of study planned today`,
  ].join(" · ");

  if (data.setup && !data.setup.has_data) {
    view.replaceChildren(
      el("section", { class: "hero" },
        el("div", {},
          el("p", { class: "eyebrow" }, data.now),
          el("h1", {}, `${greeting(data.now_iso)}. Let's set up your semester.`),
          el("p", { class: "lead" }, "Add your courses with their assignments, projects and exams. Study Agent then plans your days around your classes and warns you before a deadline slips."),
        ),
        el("div", { class: "row" },
          el("button", { class: "primary large", onclick: () => navigate("courses") }, icon("plus"), "Add your courses"),
          el("button", { class: "ghost large", onclick: () => navigate("settings") }, "Import a calendar instead"),
        ),
      ),
      el("section", { class: "stats three" },
        stat("1. Courses", "Add", "code and name of each course"),
        stat("2. Deadlines", "Date + effort", "assignments, projects, exams"),
        stat("3. Classes", "Weekly", "lectures and exercise sessions"),
      ),
    );
    return;
  }

  view.replaceChildren(
    el("section", { class: "hero" },
      el("div", {},
        el("p", { class: "eyebrow" }, data.now),
        el("h1", {}, `${greeting(data.now_iso)}.`),
        el("p", { class: "lead" }, summary),
      ),
      el("button", { class: "primary large", onclick: () => navigate("assistant", { ask: "Give me my morning brief for today." }) }, icon("sparkle"), "Morning brief"),
    ),
    el("section", { class: "stats" },
      stat("Due this week", String(s.due_this_week), "next 7 days"),
      stat("Needs attention", String(s.at_risk), `${s.tight} more tight`, s.at_risk ? "danger" : "good"),
      stat("Planned today", hours(s.planned_today_min), "saved + suggested"),
      stat("Logged this week", hours(s.logged_week_min), `${s.done_week} item${s.done_week === 1 ? "" : "s"} finished`),
    ),
    el("div", { class: "grid-2" },
      el("article", { class: "card" },
        el("div", { class: "card-head" },
          el("div", {}, el("h2", {}, `Today · ${day.day}`), el("p", { class: "muted small" }, `Study window ${day.study_window}. Suggestions fill free time, earliest deadline first.`)),
          suggested.length ? el("button", { class: "small", onclick: () => saveBlocks(suggested) }, icon("check", 14), "Save all") : null,
        ),
        timeline(day),
      ),
      el("div", { class: "stack" },
        el("article", { class: "card" },
          el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Needs attention"), el("p", { class: "muted small" }, "Work needed vs. free study time before each deadline."))),
          attention(day.at_risk),
        ),
        el("article", { class: "card" },
          el("div", { class: "card-head" }, el("h2", {}, "Coming up"), el("button", { class: "small ghost", onclick: () => navigate("deadlines") }, "All deadlines")),
          comingUp(data.coming_up),
        ),
      ),
    ),
  );
  if (data.warnings.length) data.warnings.forEach((w) => toast(w, "error"));
}
