// Theme preference: a per-browser convenience, so localStorage is fine (wrapped: it can throw).

export function getTheme() {
  try {
    return localStorage.getItem("study-theme") || "system";
  } catch {
    return "system";
  }
}

export function setTheme(theme) {
  try {
    localStorage.setItem("study-theme", theme);
  } catch {
    /* private mode: keep the in-page setting only */
  }
  applyTheme(theme);
}

export function applyTheme(theme = getTheme()) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}
