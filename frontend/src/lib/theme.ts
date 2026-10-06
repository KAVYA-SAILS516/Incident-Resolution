// Light / dark theme, chosen in the header. Light is the default; the choice is remembered per browser.
export type ThemeChoice = "light" | "dark";

const KEY = "incident-ui-theme";

export function loadTheme(): ThemeChoice {
  try {
    return window.localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.setAttribute("data-theme", choice);
  try {
    window.localStorage.setItem(KEY, choice);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}
