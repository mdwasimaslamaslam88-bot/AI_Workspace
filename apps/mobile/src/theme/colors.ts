import type { ColorSchemeName } from "react-native";

export type MobileAppearancePreference = "light" | "dark" | "system";

export function resolvedAppearanceScheme(
  preference: MobileAppearancePreference,
  system: ColorSchemeName | null | undefined,
): "light" | "dark" {
  if (preference !== "system") return preference;
  return system === "light" ? "light" : "dark";
}

export interface WorkStationColors {
  background: string;
  panel: string;
  raised: string;
  soft: string;
  line: string;
  text: string;
  muted: string;
  subtle: string;
  accent: string;
  accentSoft: string;
  accentBorder: string;
  onAccent: string;
  danger: string;
  dangerSoft: string;
}

const dark: WorkStationColors = {
  background: "#040c1f",
  panel: "#07152f",
  raised: "#09152a",
  soft: "#14243c",
  line: "#263b58",
  text: "#e8edf4",
  muted: "#9ba9ba",
  subtle: "#718199",
  accent: "#68efc8",
  accentSoft: "#0a4b43",
  accentBorder: "#1d6d62",
  onAccent: "#04251e",
  danger: "#ffb4ab",
  dangerSoft: "#3c1e22",
};

const light: WorkStationColors = {
  background: "#f4f7fa",
  panel: "#ffffff",
  raised: "#ffffff",
  soft: "#e7eef5",
  line: "#bac8d7",
  text: "#102034",
  muted: "#52657a",
  subtle: "#65788d",
  accent: "#087a68",
  accentSoft: "#ccefe7",
  accentBorder: "#56a99a",
  onAccent: "#ffffff",
  danger: "#9e302b",
  dangerSoft: "#fee9e7",
};

export function workStationColors(
  scheme: ColorSchemeName | null | undefined,
): WorkStationColors {
  return scheme === "light" ? light : dark;
}
