import { api } from "../api.js";
import { badge, courseHue, el, icon, modal, toast } from "../ui.js";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const KIND_LABEL = { assignment: "Assignment", project: "Project", exam: "Exam", quiz: "Quiz", lab: "Lab", presentation: "Presentation", other: "Other" };
const DEFAULT_HOURS = { assignment: 4, project: 15, exam: 10, quiz: 1, lab: 4, presentation: 3, other: 3 };

const changed = () => window.dispatchEvent(new CustomEvent("data-changed"));

function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

async function send(method, path, body, success) {
  try {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Study-Agent": "1" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail?.[0]?.msg || data.detail || response.statusText);
    if (success) toast(success, "success");
    changed();
    return data;
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

// --- dialogs (also used by the Calendar page) ----------------------------------------------------

export async function addCourseDialog() {
  const form = el("form", { id: "course-form", class: "form" },
    el("label", {}, "Course code", el("input", { name: "code", required: true, maxlength: "20", placeholder: "CS-E4780", autocomplete: "off" })),
    el("label", {}, "Course name", el("input", { name: "name", maxlength: "120", placeholder: "Scalable Systems and Data Management" })),
    el("p", { class: "muted small" }, "Use the official code (e.g. CS-E4780) so the assistant and the grounding check recognise it."),
  );
  const values = await modal({ title: "Add a course", body: form, actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: "Add course", kind: "primary", submit: true }] });
  if (!values) return null;
  return send("POST", "/api/courses", { code: values.code, name: values.name }, `Added ${values.code.toUpperCase()}`);
}

export async function deadlineDialog({ courses, kinds, course = "", date = "", existing = null }) {
  if (!courses.length) {
    toast("Add a course first.", "error");
    location.hash = "#/courses";
    return null;
  }
  const kindSelect = el("select", { name: "kind" }, kinds.map((k) => el("option", { value: k, selected: (existing?.kind || "assignment") === k }, KIND_LABEL[k] || k)));
  const timeInput = el("input", { name: "time", type: "time", value: existing ? existing.due.slice(11, 16) : "23:59", required: true });
  const estimate = el("input", { name: "hours", type: "number", min: "0.5", max: "100", step: "0.5", placeholder: `default ${DEFAULT_HOURS[existing?.kind || "assignment"]} h` });
  let timeTouched = Boolean(existing);
  timeInput.addEventListener("input", () => (timeTouched = true));
  kindSelect.addEventListener("change", () => {
    estimate.placeholder = `default ${DEFAULT_HOURS[kindSelect.value]} h`;
    if (!timeTouched) timeInput.value = kindSelect.value === "exam" ? "09:00" : "23:59"; // exams are in the morning
  });
  const form = el("form", { id: "deadline-form", class: "form" },
    existing ? null : el("label", {}, "Course", el("select", { name: "course", required: true }, courses.map((c) => el("option", { value: c.code, selected: c.code === course }, `${c.code} · ${c.name}`)))),
    el("div", { class: "row" }, el("label", {}, "Type", kindSelect), el("label", { class: "grow" }, "Title", el("input", { name: "title", required: true, maxlength: "200", placeholder: "e.g. Assignment 3, Final exam", value: existing?.title || "" }))),
    el("div", { class: "row" },
      el("label", {}, "Date", el("input", { name: "date", type: "date", required: true, value: existing ? existing.due.slice(0, 10) : date })),
      el("label", {}, "Time", timeInput),
      el("label", {}, "Effort (hours)", estimate),
      el("label", {}, "Grade weight (%)", el("input", { name: "weight", type: "number", min: "0", max: "100", step: "1", placeholder: "optional", value: existing?.weight_pct ?? "" })),
    ),
    el("p", { class: "muted small" }, "Effort drives the risk check and the planner (empty = default for the type). Grade weight, from the course's grading criteria, helps the assistant tell you what matters most."),
  );
  const values = await modal({
    title: existing ? `Edit ${existing.title}` : "Add a deadline",
    body: form,
    actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: existing ? "Save" : "Add", kind: "primary", submit: true }],
  });
  if (!values) return null;
  const body = { kind: values.kind, title: values.title, due: `${values.date}T${values.time}` };
  if (values.hours) body.estimate_min = Math.round(Number(values.hours) * 60);
  if (values.weight !== "") body.weight_pct = Number(values.weight);
  if (existing) return send("PATCH", `/api/deadlines/${existing.id}`, body, "Deadline updated");
  return send("POST", `/api/courses/${encodeURIComponent(values.course)}/deadlines`, body, `Added ${values.title}`);
}

async function sessionDialog(course, kinds, today) {
  const form = el("form", { id: "session-form", class: "form" },
    el("div", { class: "row" },
      el("label", {}, "Type", el("select", { name: "kind" }, kinds.map((k) => el("option", { value: k }, k[0].toUpperCase() + k.slice(1))))),
      el("label", {}, "Day", el("select", { name: "weekday" }, WEEKDAYS.map((d, i) => el("option", { value: String(i) }, d)))),
    ),
    el("div", { class: "row" },
      el("label", {}, "Starts", el("input", { name: "start", type: "time", value: "10:15", required: true })),
      el("label", {}, "Ends", el("input", { name: "end", type: "time", value: "12:00", required: true })),
    ),
    el("div", { class: "row" },
      el("label", {}, "First week", el("input", { name: "first_day", type: "date", value: today, required: true })),
      el("label", {}, "Last week", el("input", { name: "last_day", type: "date", value: addDays(today, 49), required: true })),
    ),
    el("p", { class: "muted small" }, "Repeats every week between these dates. The planner keeps study blocks out of these times."),
  );
  const values = await modal({ title: `Weekly class for ${course.code}`, body: form, actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: "Add class", kind: "primary", submit: true }] });
  if (!values) return;
  await send("POST", `/api/courses/${encodeURIComponent(course.code)}/sessions`, { ...values, weekday: Number(values.weekday) }, "Class added");
}

async function confirmDelete(title, text) {
  return modal({ title, body: el("p", {}, text), actions: [{ label: "Cancel", kind: "ghost", value: false }, { label: "Delete", kind: "danger", value: true }] });
}

// --- page ----------------------------------------------------------------------------------------

function courseCard(course, data) {
  const hue = courseHue(course.code);
  const open = course.deadlines.filter((d) => !d.done);
  const next = open[0];

  const deadlineRows = course.deadlines.length
    ? el("ul", { class: "list" }, course.deadlines.map((d) =>
        el("li", { class: `list-row deadline-row ${d.done ? "done" : ""}` },
          el("span", { class: `kind-pill ${d.kind}` }, KIND_LABEL[d.kind] || d.kind),
          el("span", { class: "grow" }, d.title),
          d.weight_pct != null ? el("span", { class: "weight", title: "Share of the course grade" }, `${d.weight_pct}%`) : null,
          el("span", { class: "when" }, d.due_local),
          d.done ? el("span", { class: "pill good" }, "Done") : badge(d.risk),
          el("button", { class: "icon-button", title: "Edit", "aria-label": `Edit ${d.title}`, onclick: () => deadlineDialog({ courses: data.courses, kinds: data.kinds, existing: d }) }, icon("edit", 15)),
          el("button", { class: "icon-button", title: "Delete", "aria-label": `Delete ${d.title}`, onclick: async () => {
            if (await confirmDelete("Delete deadline", `Delete "${d.title}"? Its study blocks are removed too; logged progress stays in your history.`)) send("DELETE", `/api/deadlines/${d.id}`, null, "Deadline deleted");
          } }, icon("trash", 15)),
        )))
    : el("p", { class: "muted small" }, "No deadlines yet. Add assignments, projects and exams with their dates.");

  const sessionRows = course.sessions.length
    ? el("ul", { class: "list" }, course.sessions.map((s) =>
        el("li", { class: "list-row" },
          el("span", { class: "when" }, `${WEEKDAYS[s.weekday].slice(0, 3)} ${s.start}–${s.end}`),
          el("span", { class: "grow" }, s.kind),
          el("span", { class: "muted small" }, `${s.first_day.slice(5).split("-").reverse().join(".")}. – ${s.last_day.slice(5).split("-").reverse().join(".")}.`),
          el("button", { class: "icon-button", title: "Delete", "aria-label": "Delete class", onclick: async () => {
            if (await confirmDelete("Delete weekly class", `Delete the ${s.kind} on ${WEEKDAYS[s.weekday]}s?`)) send("DELETE", `/api/sessions/${s.id}`, null, "Class deleted");
          } }, icon("trash", 15)),
        )))
    : el("p", { class: "muted small" }, "No weekly classes. Add lectures and exercise sessions so the planner works around them.");

  return el("article", { class: "card course-card", style: `--hue:${hue}` },
    el("div", { class: "course-head" },
      el("span", { class: "course-dot" }),
      el("div", { class: "grow" },
        el("div", { class: "row tight" }, el("span", { class: "course" }, course.code), el("h2", {}, course.name)),
        el("p", { class: "muted small" }, `${open.length} open · ${course.sessions.length} weekly class${course.sessions.length === 1 ? "" : "es"}${next ? ` · next: ${next.title}, ${next.due_local}` : ""}`),
      ),
      el("button", { class: "icon-button", title: "Rename", "aria-label": "Rename course", onclick: async () => {
        const form = el("form", { id: "rename-form", class: "form" }, el("label", {}, "Course name", el("input", { name: "name", value: course.name, required: true, maxlength: "120" })));
        const values = await modal({ title: `Rename ${course.code}`, body: form, actions: [{ label: "Cancel", kind: "ghost", value: null }, { label: "Save", kind: "primary", submit: true }] });
        if (values) send("PATCH", `/api/courses/${encodeURIComponent(course.code)}`, { name: values.name }, "Renamed");
      } }, icon("edit", 16)),
      el("button", { class: "icon-button", title: "Delete course", "aria-label": "Delete course", onclick: async () => {
        if (await confirmDelete(`Delete ${course.code}`, `Delete ${course.code} with its ${course.deadlines.length} deadline(s) and ${course.sessions.length} weekly class(es)? This cannot be undone.`)) send("DELETE", `/api/courses/${encodeURIComponent(course.code)}`, null, `${course.code} deleted`);
      } }, icon("trash", 16)),
    ),
    el("div", { class: "course-section" },
      el("div", { class: "row between" }, el("h3", {}, "Deadlines and exams"),
        el("button", { class: "small", onclick: () => deadlineDialog({ courses: data.courses, kinds: data.kinds, course: course.code, date: data.today }) }, icon("plus", 14), "Add")),
      deadlineRows,
    ),
    el("div", { class: "course-section" },
      el("div", { class: "row between" }, el("h3", {}, "Weekly classes"),
        el("button", { class: "small ghost", onclick: () => sessionDialog(course, data.session_kinds, data.today) }, icon("plus", 14), "Add class")),
      sessionRows,
    ),
  );
}

export async function render(view) {
  const data = await api.get("/api/courses");
  const head = el("section", { class: "page-head" },
    el("div", {}, el("h1", {}, "Courses"),
      el("p", { class: "lead" }, "Your courses with their assignments, projects and exams, and the weekly classes. Everything else, from risk and planning to the calendar and the assistant, works from what you enter here.")),
    el("button", { class: "primary", onclick: addCourseDialog }, icon("plus", 16), "Add course"),
  );

  if (!data.courses.length) {
    view.replaceChildren(head, el("article", { class: "card onboarding" },
      el("h2", {}, "Set up your semester in three steps"),
      el("ol", { class: "steps" },
        el("li", {}, el("strong", {}, "Add each course"), " with its code and name."),
        el("li", {}, el("strong", {}, "Add its deadlines:"), " assignments, projects, exams and quizzes, with the date, time and roughly how many hours each takes."),
        el("li", {}, el("strong", {}, "Add the weekly classes"), " (lectures, exercises) so study time is planned around them."),
      ),
      el("p", { class: "muted" }, "Then open Today, Calendar or Planner: the risk check and study plan update from your real data. Prefer importing? Set a MyCourses calendar link in .env; both can be used together."),
      el("button", { class: "primary large", onclick: addCourseDialog }, icon("plus", 16), "Add your first course"),
    ));
    return;
  }

  view.replaceChildren(
    head,
    data.imported_courses.length
      ? el("p", { class: "muted small" }, `Also imported from your calendar file: ${data.imported_courses.join(", ")}.`)
      : el("span"),
    el("div", { class: "course-grid" }, data.courses.map((c) => courseCard(c, data))),
  );
}

