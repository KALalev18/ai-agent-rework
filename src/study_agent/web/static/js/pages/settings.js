import { api } from "../api.js";
import { el, icon, toast } from "../ui.js";
import { getTheme, setTheme } from "../theme.js";

function row(label, value, hint) {
  return el("div", { class: "setting" }, el("div", {}, el("strong", {}, label), hint ? el("p", { class: "muted small" }, hint) : null), el("span", { class: "setting-value" }, value));
}

export async function render(view) {
  const s = await api.get("/api/settings");
  const syncButton = el("button", { class: "primary", disabled: s.mode === "demo" }, icon("refresh", 16), "Sync calendars now");
  syncButton.addEventListener("click", async () => {
    syncButton.disabled = true;
    try {
      const result = await api.post("/api/sync");
      const counts = Object.entries(result.counts).map(([k, v]) => `${v} ${k.replaceAll("_", " ")}`).join(", ");
      toast(`Synced: ${counts}`, "success");
      result.warnings.forEach((w) => toast(w, "error"));
      window.dispatchEvent(new CustomEvent("data-changed", { detail: { quiet: true } }));
    } catch (error) {
      toast(error.message, "error");
    } finally {
      syncButton.disabled = false;
    }
  });

  const themeSelect = el("select", { onchange: (e) => setTheme(e.target.value) },
    ["system", "light", "dark"].map((t) => el("option", { value: t, selected: getTheme() === t }, t[0].toUpperCase() + t.slice(1))));

  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Settings"), el("p", { class: "lead" }, "Configured in .env (never committed). Restart `study web` after changing it.")),
    ),
    el("div", { class: "grid-2" },
      el("article", { class: "card" }, el("h2", {}, "Calendars"),
        row("Deadlines", s.deadlines_source, "MyCourses export. A private URL is never shown, logged or sent to the model."),
        row("Timetable", s.timetable_source, ".ics URL or file, or a weekly YAML file."),
        row("Re-download after", `${s.sync_max_age_hours} h`, "Older caches are refreshed automatically."),
        el("div", { class: "setting-actions" }, syncButton, s.mode === "demo" ? el("span", { class: "muted small" }, "Demo mode uses bundled calendars.") : null),
      ),
      el("article", { class: "card" }, el("h2", {}, "Planning"),
        row("Study window", s.study_window, "Suggestions are only placed inside this window."),
        row("Daily study cap", `${s.daily_cap_min / 60} h`, "Used for risk and for suggestions."),
        row("Longest block", `${s.max_block_min} min`, "A 15-minute break follows each block."),
        row("Appearance", themeSelect),
      ),
      el("article", { class: "card" }, el("h2", {}, "Model"),
        row("Model", s.model),
        row("Effort", s.effort, "Lower effort is cheaper and faster."),
        row("Max model calls per question", String(s.max_rounds), "Hard limit, so a confused model can't loop."),
        row("Data folder", s.data_dir, "SQLite database and calendar cache."),
      ),
      el("article", { class: "card" }, el("h2", {}, "Tools the agent can use"),
        el("p", { class: "muted small" }, "Read tools run freely. Write tools only propose; nothing is saved until you approve."),
        el("ul", { class: "tool-list" }, s.tools.map((t) => el("li", {}, el("span", { class: "mono" }, t.name), el("span", { class: `pill ${t.writes ? "warn" : "good"}` }, t.writes ? "needs approval" : "read-only")))),
      ),
    ),
  );
}
