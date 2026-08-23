import axe from "axe-core";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { IndexedDocument } from "../src/api/contracts";
import { ConnectView } from "../src/features/auth/ConnectView";
import { ChatView } from "../src/features/chat/ChatView";
import { ConversationList } from "../src/features/conversations/ConversationList";
import { ModelSelector } from "../src/features/models/ModelSelector";
import { SettingsPanel } from "../src/features/settings/SettingsPanel";
import {
  conversation,
  message,
  model,
  productCapabilities,
  systemDiagnostics,
} from "./fixtures";

async function expectNoStructuralViolations(container: HTMLElement): Promise<void> {
  const result = await axe.run(container, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    },
    rules: {
      "color-contrast": { enabled: false },
    },
  });
  expect(
    result.violations.map((violation) => ({
      id: violation.id,
      nodes: violation.nodes.map((node) => node.target),
    })),
  ).toEqual([]);
}

const indexedDocument: IndexedDocument = {
  id: "99999999-9999-4999-8999-999999999999",
  asset_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  status: "ready",
  source_state: "active",
  original_filename: "notes.txt",
  media_type: "text/plain",
  chunk_count: 1,
  character_count: 5,
  failure_code: null,
  created_at: "2026-08-22T00:00:00Z",
  updated_at: "2026-08-22T00:00:01Z",
  completed_at: "2026-08-22T00:00:01Z",
};

describe("critical surface accessibility", () => {
  it("keeps owner connection controls structurally accessible", async () => {
    const { container } = render(
      <ConnectView connecting={false} error={null} onConnect={vi.fn(async () => undefined)} />,
    );
    await expectNoStructuralViolations(container);
  });

  it("keeps conversation navigation, model choice, and chat composer accessible", async () => {
    const { container } = render(
      <div>
        <ConversationList
          conversations={[conversation]}
          userId="11111111-1111-4111-8111-111111111111"
          selectedId={conversation.id}
          nextCursor={null}
          loading={false}
          loadingMore={false}
          error={null}
          showArchived={false}
          searchQuery=""
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn(async () => undefined)}
          onUpdateState={vi.fn(async () => undefined)}
          onDuplicate={vi.fn(async () => undefined)}
          onDelete={vi.fn(async () => undefined)}
          onShowArchivedChange={vi.fn()}
          onSearchQueryChange={vi.fn()}
          onReload={vi.fn()}
          onLoadMore={vi.fn()}
          onLogout={vi.fn()}
        />
        <main>
          <ModelSelector
            models={[model]}
            selectedModelId={model.model_id}
            loading={false}
            error={null}
            onSelect={vi.fn()}
            onReload={vi.fn()}
          />
          <ChatView
            conversation={conversation}
            creatingNew={false}
            canGenerate
            messages={[message(1, "user", "hello"), message(2, "assistant", "ready")]}
            nextCursor={null}
            loadingMessages={false}
            loadingMoreMessages={false}
            creatingConversation={false}
            generating={false}
            notice={null}
            onCreateConversation={vi.fn(async () => undefined)}
            onCancelNew={vi.fn()}
            onGenerate={vi.fn(async () => undefined)}
            onEditAndResend={vi.fn(async () => undefined)}
            onRegenerate={vi.fn(async () => undefined)}
            onCancelGeneration={vi.fn()}
            onLoadMoreMessages={vi.fn()}
            onReloadMessages={vi.fn()}
            onUploadAttachment={vi.fn(async () => Promise.reject(new Error("unused")))}
            onIngestDocument={vi.fn(async () => indexedDocument)}
            onDownloadAttachment={vi.fn(async () => new Blob())}
            onDeleteAttachment={vi.fn(async () => undefined)}
          />
        </main>
      </div>,
    );
    await expectNoStructuralViolations(container);
  });

  it("keeps account, appearance, capability, and diagnostics settings accessible", async () => {
    const { container } = render(
      <main>
        <SettingsPanel
          onClose={vi.fn()}
          onLoad={vi.fn(async () => productCapabilities)}
          onLoadDiagnostics={vi.fn(async () => systemDiagnostics)}
          onLoadSessions={vi.fn(async () => [{
            id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            label: "This browser",
            created_at: "2026-08-22T00:00:00Z",
            updated_at: "2026-08-22T00:00:00Z",
            is_current: true,
          }])}
          appearance="system"
          onAppearanceChange={vi.fn()}
          onRotateSession={vi.fn(async () => undefined)}
          onCreateSession={vi.fn(async () => Promise.reject(new Error("unused")))}
          onRenameCurrentSession={vi.fn(async () => Promise.reject(new Error("unused")))}
          onRevokeSession={vi.fn(async () => undefined)}
          onLogout={vi.fn(async () => undefined)}
          onManageMemory={vi.fn()}
        />
      </main>,
    );
    await screen.findByText("7 of 11 capabilities available now.");
    await expectNoStructuralViolations(container);
  });
});
