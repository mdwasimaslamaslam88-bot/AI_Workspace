import type { HardwareClass, LocalModel, ModelScaleClass } from "./contracts";

export type ModelReadiness =
  | "ready"
  | "not_installed"
  | "unavailable"
  | "insufficient_hardware";

const SCALE_LABELS: Record<ModelScaleClass, string> = {
  "7b_8b": "7B / 8B",
  "14b": "14B",
  "30b_34b": "30B–34B",
  "70b": "70B",
  "100b_plus": "100B+",
  "200b_plus": "200B+",
  "moe_very_large": "MoE / very large",
};

const HARDWARE_LABELS: Record<HardwareClass, string> = {
  cpu_only: "CPU",
  gpu_under_8gb: "GPU under 8 GiB",
  gpu_8_to_15gb: "GPU 8–15 GiB",
  gpu_16_to_23gb: "GPU 16–23 GiB",
  gpu_24_to_47gb: "GPU 24–47 GiB",
  gpu_48_to_79gb: "GPU 48–79 GiB",
  gpu_80gb_plus: "GPU 80 GiB+",
  multi_gpu: "Multi-GPU",
};

export const MODEL_READINESS_LABELS: Record<ModelReadiness, string> = {
  ready: "Ready",
  not_installed: "Not installed",
  unavailable: "Unavailable",
  insufficient_hardware: "Insufficient hardware",
};

export function modelReadiness(model: LocalModel): ModelReadiness {
  if (!model.installed) return "not_installed";
  if (model.availability !== "available") return "unavailable";
  return model.runnable_now ? "ready" : "insufficient_hardware";
}

export function modelScaleLabel(scale: ModelScaleClass | null): string {
  return scale === null ? "Unclassified scale" : SCALE_LABELS[scale];
}

export function modelHardwareLabel(hardware: HardwareClass | null): string {
  return hardware === null ? "Hardware requirement unreported" : HARDWARE_LABELS[hardware];
}

export function modelContextLabel(contextWindow: number | null): string {
  return contextWindow === null
    ? "Context unreported"
    : `${contextWindow.toLocaleString("en-US")} token context`;
}
