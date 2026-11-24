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

  const keyStatus = el("span", { class: `pill ${s.api_key ? "good" : "warn"}` }, s.api_key ? `Connected · ${s.api_key}` : "Not set");
  const keyInput = el("input", { type: "password", name: "api_key", placeholder: "sk-ant-...", autocomplete: "off", spellcheck: "false", class: "grow" });
  const keyButton = el("button", { type: "submit", class: "primary" }, icon("check", 16), s.api_key ? "Replace key" : "Save and check");
  const keyForm = el("form", { class: "key-form" }, keyInput, keyButton);
  keyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    keyButton.disabled = true;
    keyButton.replaceChildren(icon("refresh", 16), "Checking with Anthropic…");
    try {
      const result = await api.post("/api/settings/api-key", { api_key: keyInput.value });
      keyInput.value = "";
      keyStatus.className = "pill good";
      keyStatus.textContent = `Connected · ${result.api_key}`;
      toast("API key saved. The assistant is ready.", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      keyButton.disabled = false;
      keyButton.replaceChildren(icon("check", 16), "Save and check");
    }
  });
  const keyCard = el("article", { class: "card key-card" },
    el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Claude API key"),
      el("p", { class: "muted small" }, "Needed only for the Assistant; every other page works without it.")), keyStatus),
    s.mode === "demo"
      ? el("p", { class: "muted" }, "Demo mode replays scripted answers and does not use a key. Run `study web` (without --demo) to use your own.")
      : keyForm,
    el("p", { class: "muted small" }, "Get a key at platform.claude.com → API keys. It is checked with Anthropic, then saved in ",
      el("code", {}, s.env_file), " on this computer (gitignored, never committed). It is only sent to Anthropic, never logged, and this page only ever shows the masked form."),
  );

  view.replaceChildren(
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Settings"), el("p", { class: "lead" }, "Other settings live in .env (never committed). Restart `study web` after changing them there.")),
    ),
    keyCard,
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
