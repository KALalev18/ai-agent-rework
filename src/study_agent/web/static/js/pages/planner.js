import { api } from "../api.js";
import { confirmAction, courseHue, el, hm, icon, isoDate, modal, toast } from "../ui.js";
import { openItem } from "./deadlines.js";

const START_HOUR = 8;
const END_HOUR = 22;
const PX_PER_MIN = 0.8; // 48 px per hour
const SNAP = 15;

const minutesOf = (iso) => Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16)) - START_HOUR * 60;
const toY = (minutes) => Math.max(0, minutes) * PX_PER_MIN;
const pad = (n) => String(n).padStart(2, "0");

function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

let weekStart = null;

export async function render(view) {
  const data = await api.get(`/api/week${weekStart ? `?start=${weekStart}` : ""}`);
  weekStart = data.start;
  const columnHeight = (END_HOUR - START_HOUR) * 60 * PX_PER_MIN;
  const reload = () => render(view);

  const hoursCol = el("div", { class: "hours" },
    Array.from({ length: END_HOUR - START_HOUR }, (_, i) => el("div", { class: "hour", style: `height:${60 * PX_PER_MIN}px` }, `${pad(START_HOUR + i)}:00`)),
  );

  const [winStart, winEnd] = data.window.map((t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3)) - START_HOUR * 60);

  const columns = data.days.map((day) => {
    const isToday = day.date === data.today;
    const isPast = day.date < data.today;
    const col = el("div", { class: `day-col ${isToday ? "today" : ""} ${isPast ? "past" : ""}`, style: `height:${columnHeight}px`, "data-date": day.date });
    col.append(el("div", { class: "window", style: `top:${toY(winStart)}px;height:${toY(winEnd) - toY(winStart)}px` }));

    const place = (startIso, endIso) => `top:${toY(minutesOf(startIso))}px;height:${Math.max(18, (minutesOf(endIso) - minutesOf(startIso)) * PX_PER_MIN - 2)}px`;
    // Blocks under 45 minutes only have room for one line.
    const size = (startIso, endIso) => (minutesOf(endIso) - minutesOf(startIso) < 45 ? "short" : "");

    for (const e of data.events.filter((x) => isoDate(x.start) === day.date)) {
      col.append(el("div", { class: `block event ${size(e.start, e.end)}`, style: place(e.start, e.end), title: `${e.title} ${hm(e.start)}–${hm(e.end)}` },
        el("span", { class: "block-title" }, e.title), el("span", { class: "block-time" }, `${hm(e.start)}–${hm(e.end)} · ${e.kind}`)));
    }
    for (const s of data.suggested.filter((x) => isoDate(x.start) === day.date)) {
      col.append(el("button", {
        class: `block suggested ${size(s.start, s.end)}`, style: `${place(s.start, s.end)};--hue:${courseHue(s.course)}`,
        title: `Suggested: ${s.course} ${s.title}. Click to save.`,
        onclick: async () => { await confirmAction("schedule_blocks", { blocks: [{ item_id: s.item_id, start: s.start.slice(0, 16), minutes: s.minutes }] }, { silent: true }); },
      }, el("span", { class: "block-title" }, `+ ${s.course} ${s.title}`), el("span", { class: "block-time" }, `${hm(s.start)}–${hm(s.end)} · suggested`)));
    }
    for (const b of data.saved.filter((x) => isoDate(x.start) === day.date)) {
      const block = el("div", {
        class: `block saved ${size(b.start, b.end)}`, style: `${place(b.start, b.end)};--hue:${courseHue(b.course)}`, draggable: "true", tabindex: "0",
        title: `${b.block_id} ${b.course} ${b.title}. Drag to move, click for options.`,
        onclick: () => blockMenu(b),
      }, el("span", { class: "block-title" }, `${b.course} ${b.title}`), el("span", { class: "block-time" }, `${hm(b.start)}–${hm(b.end)} · ${b.block_id}`));
      block.addEventListener("dragstart", (event) => {
        const grab = event.clientY - block.getBoundingClientRect().top;
        event.dataTransfer.setData("text/plain", JSON.stringify({ id: b.block_id, grab, minutes: b.minutes }));
        event.dataTransfer.effectAllowed = "move";
        requestAnimationFrame(() => block.classList.add("dragging"));
      });
      block.addEventListener("dragend", () => block.classList.remove("dragging"));
      col.append(block);
    }
    for (const d of data.deadlines.filter((x) => isoDate(x.due) === day.date)) {
      // Centre the flag on the due time, but keep it inside the grid (23:59 is below 22:00).
      const y = Math.min(Math.max(0, toY(minutesOf(d.due)) - 9), columnHeight - 18);
      col.append(el("button", { class: `deadline-flag ${d.risk}`, style: `top:${y}px`, title: `${d.course} ${d.title} due ${d.due_local}`, onclick: () => openItem(d.id) },
        icon("flag", 12), `${d.course} due ${hm(d.due)}`));
    }
    if (isToday) col.append(el("div", { class: "now-line", style: `top:${toY(minutesOf(data.now))}px` }));

    // Drop target: snap to 15 minutes and ask the server to move the block.
    const ghost = el("div", { class: "drop-ghost" });
    col.addEventListener("dragover", (event) => {
      event.preventDefault();
      const info = col.dataset.drag ? JSON.parse(col.dataset.drag) : null;
      const top = event.clientY - col.getBoundingClientRect().top - (info?.grab || 0);
      const minutes = Math.round(top / PX_PER_MIN / SNAP) * SNAP;
      ghost.style.top = `${toY(minutes)}px`;
      ghost.style.height = `${(info?.minutes || 60) * PX_PER_MIN}px`;
      if (!ghost.isConnected) col.append(ghost);
    });
    col.addEventListener("dragleave", () => ghost.remove());
    col.addEventListener("drop", async (event) => {
      event.preventDefault();
      ghost.remove();
      const info = JSON.parse(event.dataTransfer.getData("text/plain"));
      const top = event.clientY - col.getBoundingClientRect().top - info.grab;
      const minutes = Math.max(0, Math.round(top / PX_PER_MIN / SNAP) * SNAP) + START_HOUR * 60;
      const start = `${day.date}T${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
      await confirmAction("move_study_block", { block_id: info.id, new_start: start }, { silent: true });
    });
    return col;
  });

  // Share the grab offset with every column during a drag (dataTransfer is unreadable in dragover).
  const grid = el("div", { class: "week-grid" }, hoursCol, columns);
  grid.addEventListener("dragstart", (event) => {
    const payload = event.dataTransfer.getData("text/plain");
    for (const c of columns) c.dataset.drag = payload;
  });
  grid.addEventListener("dragend", () => columns.forEach((c) => delete c.dataset.drag));

  async function blockMenu(b) {
    const form = el("form", { id: "move-form", class: "form" },
      el("p", { class: "muted" }, `${b.when} · ${b.minutes} min`),
      el("div", { class: "row" },
        el("label", {}, "Date", el("input", { name: "date", type: "date", value: isoDate(b.start), required: true })),
        el("label", {}, "Start", el("input", { name: "time", type: "time", step: "900", value: hm(b.start), required: true })),
        el("label", {}, "Minutes", el("input", { name: "minutes", type: "number", min: "15", max: "480", step: "15", value: b.minutes, required: true })),
      ),
    );
    const choice = await modal({
      title: `${b.course} ${b.title}`,
      body: form,
      actions: [
        { label: "Remove", kind: "danger", value: "remove" },
        { label: "Open item", kind: "ghost", value: "open" },
        { label: "Move", kind: "primary", submit: true },
      ],
    });
    if (choice === "remove") {
      await confirmAction("delete_study_block", { block_id: b.block_id });
    } else if (choice === "open") {
      openItem(b.item_id);
    } else if (choice && choice.date) {
      await confirmAction("move_study_block", { block_id: b.block_id, new_start: `${choice.date}T${choice.time}`, minutes: Number(choice.minutes) });
    }
  }

  const saveAll = async () => {
    if (!data.suggested.length) return toast("No suggestions left this week.");
    const blocks = data.suggested.slice(0, 50).map((s) => ({ item_id: s.item_id, start: s.start.slice(0, 16), minutes: s.minutes }));
    await confirmAction("schedule_blocks", { blocks });
  };

  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Planner"), el("p", { class: "lead" }, "Drag your saved blocks to move them. Click a dashed suggestion to save it. Lectures and deadlines come from your calendars.")),
      el("div", { class: "week-nav" },
        el("button", { class: "icon-button", "aria-label": "Previous week", onclick: () => { weekStart = addDays(weekStart, -7); reload(); } }, icon("chevronLeft")),
        el("strong", {}, data.label),
        el("button", { class: "icon-button", "aria-label": "Next week", onclick: () => { weekStart = addDays(weekStart, 7); reload(); } }, icon("chevronRight")),
        el("button", { class: "ghost small", onclick: () => { weekStart = null; reload(); } }, "This week"),
        el("button", { class: "small", onclick: saveAll }, icon("check", 14), `Save suggestions (${data.suggested.length})`),
      ),
    ),
    el("div", { class: "legend" },
      el("span", { class: "key event" }, "Lecture / session"),
      el("span", { class: "key saved" }, "Saved study block"),
      el("span", { class: "key suggested" }, "Suggestion"),
      el("span", { class: "key flag" }, "Deadline"),
    ),
    el("article", { class: "card flush planner-card" },
      el("div", { class: "week-head" }, el("div", {}), data.days.map((d) => el("div", { class: `day-head ${d.date === data.today ? "today" : ""}` }, d.label))),
      el("div", { class: "week-scroll" }, grid),
    ),
  );
}
