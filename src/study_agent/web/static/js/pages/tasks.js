import { api } from "../api.js";
import { confirmAction, el, icon } from "../ui.js";
import { render as renderList } from "./deadlines.js";

export async function render(view, ctx) {
  await renderList(view, { ...ctx, kind: "task" });
  const { courses } = await api.get("/api/items");

  const form = el("form", { class: "quick-add" },
    el("input", { name: "title", placeholder: "Add a task, e.g. Email the TA about grading", required: true, maxlength: "200" }),
    el("input", { name: "course", placeholder: "Course", list: "course-list", maxlength: "20", class: "narrow" }),
    el("datalist", { id: "course-list" }, courses.map((c) => el("option", { value: c }))),
    el("input", { name: "due", type: "date", title: "Due date (optional)", class: "narrow" }),
    el("input", { name: "estimate_min", type: "number", min: "5", max: "6000", step: "5", placeholder: "Minutes", class: "narrow" }),
    el("button", { type: "submit", class: "primary" }, icon("plus", 16), "Add"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const args = { title: values.title.trim() };
    if (values.course) args.course = values.course.trim();
    if (values.due) args.due = values.due;
    if (values.estimate_min) args.estimate_min = Number(values.estimate_min);
    if (await confirmAction("add_task", args, { silent: true })) form.reset();
  });
  view.querySelector(".toolbar").before(form);
}
