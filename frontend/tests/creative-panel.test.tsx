import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { CreativeCapabilities, CreativeExperience } from "../src/api/contracts";
import { CreativePanel } from "../src/features/creative/CreativePanel";
import { rawSecret } from "./fixtures";

const experienceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const timestamp = "2026-09-03T00:00:00Z";
const capabilities: CreativeCapabilities = {
  interactive_stories: true, text_games: true, fictional_characters: true,
  verified_local_text_generation: true, general_audience_only: true,
  image_generation_status: "runtime_dependent", voice_status: "runtime_dependent",
  video_generation_status: "external_dependency", animation_status: "external_dependency",
  audio_generation_editing_status: "external_dependency", adult_experience_status: "external_dependency",
  external_dependencies: ["verified_video_animation_or_audio_runtime", "jurisdiction_check", "age_verification", "consent_policy"],
};

function experience(withTurn = false): CreativeExperience {
  return {
    id: experienceId, mode: "story", title: "The Signal", premise: "A quiet observatory.",
    genre: "science fiction", language: "en", character_name: null, safety_tier: "general",
    status: "active", turn_count: withTurn ? 1 : 0, created_at: timestamp, updated_at: timestamp,
    completed_at: null, turns: withTurn ? [{ id: "turn", position: 1, owner_input: "Open the door.",
      output: "The observatory door opens.", output_sha256: "a".repeat(64), model_id: "local/qwen3", created_at: timestamp }] : [],
  };
}

function props(values: CreativeExperience[] = [experience()]) {
  return {
    onClose: vi.fn(), onCapabilities: vi.fn(async () => capabilities), onLoad: vi.fn(async () => values),
    onGet: vi.fn(async () => experience()), onCreate: vi.fn(async () => experience()),
    onTurn: vi.fn(async () => experience(true)), onComplete: vi.fn(async () => ({ ...experience(true), status: "completed" as const, completed_at: timestamp })),
  };
}

describe("CreativePanel", () => {
  it("creates a bounded fictional-character experience", async () => {
    const actions = props([]);
    render(<CreativePanel {...actions} />);
    await userEvent.selectOptions(await screen.findByLabelText("Mode"), "character");
    await userEvent.type(screen.getByLabelText("Title"), "Tea at Dawn");
    await userEvent.type(screen.getByLabelText("Premise"), "A calm conversation.");
    await userEvent.type(screen.getByLabelText("Genre"), "slice of life");
    await userEvent.type(screen.getByLabelText("Fictional character name"), "Mira");
    await userEvent.click(screen.getByRole("button", { name: "Create experience" }));
    expect(actions.onCreate).toHaveBeenCalledWith(expect.objectContaining({ mode: "character", character_name: "Mira", language: "en" }), expect.any(AbortSignal));
  });

  it("submits untouched input and shows verification evidence", async () => {
    const actions = props();
    render(<CreativePanel {...actions} />);
    const form = (await screen.findByLabelText("Your next move")).closest("form");
    expect(form).not.toBeNull();
    await userEvent.type(within(form as HTMLElement).getByLabelText("Your next move"), "Open the door.");
    await userEvent.click(within(form as HTMLElement).getByRole("button", { name: "Generate verified turn" }));
    await waitFor(() => expect(actions.onTurn).toHaveBeenCalledWith(experienceId, { owner_input: "Open the door." }, expect.any(AbortSignal)));
    expect(await screen.findByText(/Verified artifact aaaaaaaaaaaa/)).toHaveTextContent("model local/qwen3");
  });

  it("reports boundaries and redacts failures", async () => {
    const actions = props([]);
    actions.onCreate.mockRejectedValueOnce(new Error(rawSecret));
    render(<CreativePanel {...actions} />);
    expect(await screen.findByText(/Local stories, games, and fictional characters: ready/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("Title"), "The Signal");
    await userEvent.type(screen.getByLabelText("Premise"), "A quiet observatory.");
    await userEvent.type(screen.getByLabelText("Genre"), "science fiction");
    await userEvent.click(screen.getByRole("button", { name: "Create experience" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rejected or could not be verified");
    expect(document.body.textContent).not.toContain(rawSecret);
  });
});
