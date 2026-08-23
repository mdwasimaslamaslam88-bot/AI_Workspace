import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  firstRunnableCapabilityModel,
  selectableTextModels,
} from "../src/app/collections";
import type { LocalModel } from "../src/api/contracts";
import { ModelSelector } from "../src/features/models/ModelSelector";
import { model, visionModel } from "./fixtures";

const callbacks = {
  onSelect: vi.fn(),
  onReload: vi.fn(),
};

describe("ModelSelector", () => {
  it("keeps multimodal text models selectable and finds media capabilities", () => {
    const multimodal: LocalModel = {
      ...model,
      modality: "multimodal" as const,
      capabilities: ["text_generation", "vision_input"],
    };
    const speech: LocalModel = {
      ...model,
      model_id: "piper:" + "b".repeat(24),
      runtime_id: "piper",
      modality: "audio" as const,
      capabilities: ["speech_synthesis"],
    };

    expect(selectableTextModels([multimodal])).toEqual([multimodal]);
    expect(firstRunnableCapabilityModel([multimodal, speech], "speech_synthesis"))
      .toBe(speech);
  });
  it("shows loading and empty states", () => {
    const { rerender } = render(
      <ModelSelector
        models={[]}
        selectedModelId={null}
        loading
        error={null}
        {...callbacks}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Discovering");
    rerender(
      <ModelSelector
        models={[]}
        selectedModelId={null}
        loading={false}
        error={null}
        {...callbacks}
      />,
    );
    expect(screen.getByText(/allowlist a local text-generation model/i)).toBeVisible();
  });

  it("selects only available models with executable text generation", async () => {
    const onSelect = vi.fn();
    const unavailable = { ...model, model_id: "ollama-local:bbbbbbbbbbbbbbbbbbbbbbbb", availability: "unavailable" as const };
    const embedding = {
      ...model,
      model_id: "ollama-local:cccccccccccccccccccccccc",
      capabilities: ["embeddings" as const],
    };
    const insufficientHardware = {
      ...model,
      model_id: "ollama-local:eeeeeeeeeeeeeeeeeeeeeeee",
      runnable_now: false,
      fallback_model_id: model.model_id,
    };
    expect(
      selectableTextModels([
        model,
        unavailable,
        embedding,
        insufficientHardware,
      ]),
    ).toEqual([model]);

    render(
      <ModelSelector
        models={[model, unavailable, embedding]}
        selectedModelId={model.model_id}
        loading={false}
        error={null}
        onSelect={onSelect}
        onReload={vi.fn()}
      />,
    );
    expect(screen.getByRole("option", { name: /Local Model/ })).toBeVisible();
    expect(screen.queryByRole("option", { name: /embedding/i })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), model.model_id);
    expect(onSelect).toHaveBeenCalledWith(model.model_id);
    expect(screen.getByText("text generation")).toBeVisible();
  });

  it("shows a safe unavailable state and retries", async () => {
    const onReload = vi.fn();
    render(
      <ModelSelector
        models={[]}
        selectedModelId={null}
        loading={false}
        error="The local model runtime is unavailable."
        onSelect={vi.fn()}
        onReload={onReload}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("marks vision capability from public model metadata", () => {
    render(
      <ModelSelector
        models={[model, visionModel]}
        selectedModelId={visionModel.model_id}
        loading={false}
        error={null}
        {...callbacks}
      />,
    );

    expect(
      screen.getByRole("option", { name: /Local Vision Model · 8B · Vision/ }),
    ).toBeVisible();
    expect(screen.getByText("vision input")).toBeVisible();
  });
});
