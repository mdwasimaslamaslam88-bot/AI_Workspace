import {
  modelContextLabel,
  modelHardwareLabel,
  modelReadiness,
  modelScaleLabel,
  type LocalModel,
} from "@work-station/shared";
import { describe, expect, it } from "vitest";

const model: LocalModel = {
  model_id: "ollama-local:aaaaaaaaaaaaaaaaaaaaaaaa",
  display_name: "Local model",
  runtime_id: "ollama-local",
  modality: "text",
  family: "local",
  parameter_class: "34B",
  capabilities: ["chat", "text_generation", "tool_calling"],
  context_window: 32_768,
  quantization: "Q4",
  estimated_vram_bytes: null,
  availability: "available",
  scale_class: "30b_34b",
  required_vram_bytes: 24 * 1024 ** 3,
  required_ram_bytes: 32 * 1024 ** 3,
  installed: true,
  runnable_now: false,
  future_capable: true,
  hardware_class: "gpu_24_to_47gb",
  fallback_model_id: null,
};

describe("shared model presentation", () => {
  it("uses only authoritative model metadata for client labels", () => {
    expect(modelReadiness(model)).toBe("insufficient_hardware");
    expect(modelReadiness({ ...model, installed: false })).toBe("not_installed");
    expect(modelReadiness({ ...model, availability: "unavailable" })).toBe(
      "unavailable",
    );
    expect(modelReadiness({ ...model, runnable_now: true })).toBe("ready");
    expect(modelScaleLabel(model.scale_class)).toBe("30B–34B");
    expect(modelScaleLabel("500b_plus")).toBe("500B+");
    expect(modelScaleLabel("1000b_plus")).toBe("1000B+");
    expect(modelScaleLabel("2000b")).toBe("2000B");
    expect(modelContextLabel(model.context_window)).toBe("32,768 token context");
    expect(modelHardwareLabel(model.hardware_class)).toBe("GPU 24–47 GiB");
  });
});
