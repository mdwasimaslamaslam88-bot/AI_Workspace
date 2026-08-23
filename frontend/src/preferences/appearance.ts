export type AppearancePreference = "system" | "dark" | "light";

const APPEARANCE_KEY = "work-station.appearance";

export function readAppearancePreference(): AppearancePreference {
  try {
    const value = window.localStorage.getItem(APPEARANCE_KEY);
    return value === "dark" || value === "light" ? value : "system";
  } catch {
    return "system";
  }
}

export function writeAppearancePreference(value: AppearancePreference): void {
  try {
    if (value === "system") window.localStorage.removeItem(APPEARANCE_KEY);
    else window.localStorage.setItem(APPEARANCE_KEY, value);
  } catch {
    // Appearance persistence is optional and never affects the owner session.
  }
}

export function applyAppearancePreference(value: AppearancePreference): void {
  const dark =
    value === "dark" ||
    (value === "system" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
