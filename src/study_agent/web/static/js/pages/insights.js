import { api } from "../api.js";
import { el, empty, icon, toast } from "../ui.js";

function metric(label, value, hint) {
  return el("div", { class: "stat" }, el("span", { class: "stat-label" }, label), el("strong", {}, value), hint ? el("span", { class: "stat-hint" }, hint) : null);
}

async function runEval(target, button) {
  button.disabled = true;
  button.replaceChildren(icon("refresh", 16), "Running the evaluation…");
  try {
    const data = await api.post("/api/eval");
    const s = data.summary;
    target.replaceChildren(
      el("section", { class: "stats" },
        metric("Cases passed", `${s.passed} / ${s.cases}`, `sources: ${s.sources.join(", ")}`),
        metric("Correct tool calls", `${s.tool_calls_correct} / ${s.cases}`),
        metric("Ungrounded claims", String(s.ungrounded_claims_after_check), "after the checker (target 0)"),
        metric("Caught by checker", String(s.claims_caught_by_checker), "invented facts stopped"),
      ),
      el("div", { class: "table-wrap" }, el("table", { class: "data" },
        el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "Case"), el("th", {}, "Question"), el("th", {}, "Tools called"), el("th", {}, "Notes"))),
        el("tbody", {}, data.cases.map((c) => el("tr", {},
          el("td", {}, el("span", { class: `pill ${c.passed ? "good" : "danger"}` }, c.passed ? "Pass" : "Fail")),
          el("td", { class: "mono small" }, c.id),
          el("td", {}, c.question),
          el("td", { class: "mono small" }, c.tools.join(", ") || "none"),
          el("td", { class: "small muted" }, c.problems.join("; ") || (c.caught ? `checker caught ${c.caught} invented claim(s)` : "")),
        ))),
      )),
    );
    toast(`Eval finished: ${s.passed}/${s.cases} passed`, s.passed === s.cases ? "success" : "error");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.replaceChildren(icon("refresh", 16), "Run again");
  }
}

export async function render(view) {
  const { report, recent } = await api.get("/api/stats");
  const evalTarget = el("div", {}, el("p", { class: "muted" }, "Runs the evaluation set against the fictional semester in replay mode: no API key, no cost, about a second."));
  const evalButton = el("button", { class: "primary", onclick: () => runEval(evalTarget, evalButton) }, icon("sparkle", 16), "Run evaluation");

  const sections = [
    el("section", { class: "page-head" },
      el("div", {}, el("h1", {}, "Insights"), el("p", { class: "lead" }, "What the agent costs, how fast it is, and how often the grounding check had to step in. Every number comes from the run log.")),
    ),
    report
      ? el("section", { class: "stats" },
          metric("Days used", report["days used"].split(" ")[0], report["days used"].replace(/^\d+ /, "")),
          metric("Cost per day", report["cost per day used"], `total ${report["cost total"]}`),
          metric("Brief latency p50 / p95", report["`study today` latency p50 / p95"].split(" (")[0], "for study today"),
          metric("Served from cache", report["input tokens served from cache"], "input tokens"),
        )
      : el("article", { class: "card" }, empty("No live runs yet. Ask the assistant something with your API key set and the numbers appear here.", "insights")),
    report
      ? el("section", { class: "stats three" },
          metric("Runs", report["live runs"]),
          metric("Corrected by grounding check", report["answers corrected by the grounding check"]),
          metric("Still ungrounded", report["answers still ungrounded after correction"], "shown with a warning"),
        )
      : null,
    recent.length
      ? el("article", { class: "card flush" }, el("div", { class: "card-head padded" }, el("h2", {}, "Recent runs")),
          el("div", { class: "table-wrap" }, el("table", { class: "data" },
            el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "From"), el("th", {}, "Question"), el("th", {}, "Tools"), el("th", {}, "Time"), el("th", {}, "Cost"), el("th", {}, "Grounding"))),
            el("tbody", {}, recent.map((r) => el("tr", {},
              el("td", { class: "nowrap" }, r.when), el("td", {}, r.command), el("td", {}, r.question),
              el("td", {}, String(r.tool_calls)), el("td", { class: "nowrap" }, `${r.latency_s} s`), el("td", { class: "nowrap" }, `€${r.cost_eur.toFixed(3)}`),
              el("td", {}, el("span", { class: `pill ${r.grounded ? "good" : "danger"}` }, r.grounded ? (r.corrected ? "corrected" : "ok") : "warning"))))))))
      : null,
    el("article", { class: "card" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Evaluation"), el("p", { class: "muted small" }, "Tool choice, facts, grounding and database state, checked automatically.")), evalButton),
      evalTarget,
    ),
  ];
  view.replaceChildren(...sections.filter(Boolean)); // replaceChildren would print "null"
}
