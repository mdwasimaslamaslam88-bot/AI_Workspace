import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { mergeConversations } from "../src/app/collections";
import { ConversationList } from "../src/features/conversations/ConversationList";
import { conversation } from "./fixtures";

const baseProps = {
  userId: "11111111-1111-4111-8111-111111111111",
  selectedId: null,
  nextCursor: null,
  loading: false,
  loadingMore: false,
  error: null,
  showArchived: false,
  onCreate: vi.fn(),
  onSelect: vi.fn(),
  onRename: vi.fn(async () => undefined),
  onUpdateState: vi.fn(async () => undefined),
  onDelete: vi.fn(async () => undefined),
  onShowArchivedChange: vi.fn(),
  onReload: vi.fn(),
  onLoadMore: vi.fn(),
  onLogout: vi.fn(),
};

describe("ConversationList", () => {
  it("merges paginated conversations without duplicates", () => {
    const later = {
      ...conversation,
      id: "44444444-4444-4444-8444-444444444444",
      title: "Later",
    };
    expect(
      mergeConversations([conversation], [conversation, later]).map((item) => item.id),
    ).toEqual([conversation.id, later.id]);
  });

  it("renders selection and invokes create, select, and pagination actions", async () => {
    const onCreate = vi.fn();
    const onSelect = vi.fn();
    const onLoadMore = vi.fn();
    render(
      <ConversationList
        {...baseProps}
        conversations={[conversation]}
        selectedId={conversation.id}
        nextCursor={{ updated_at: conversation.updated_at, id: conversation.id }}
        onCreate={onCreate}
        onSelect={onSelect}
        onLoadMore={onLoadMore}
      />,
    );

    expect(screen.getByRole("button", { name: /Local chat/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await userEvent.click(screen.getByRole("button", { name: /Local chat/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Load more conversations" }),
    );
    expect(onCreate).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(conversation);
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("shows bounded loading, empty, and safe error states", async () => {
    const onReload = vi.fn();
    const { rerender } = render(
      <ConversationList {...baseProps} conversations={[]} loading />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading conversations");
    rerender(
      <ConversationList {...baseProps} conversations={[]} loading={false} />,
    );
    expect(screen.getByText("No conversations yet.")).toBeVisible();
    rerender(
      <ConversationList
        {...baseProps}
        conversations={[]}
        error="Could not reach the local backend."
        onReload={onReload}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("searches loaded chats and exposes confirmed rename and delete actions", async () => {
    const second = {
      ...conversation,
      id: "44444444-4444-4444-8444-444444444444",
      title: "Hardware plan",
    };
    const onRename = vi.fn(async () => undefined);
    const onDelete = vi.fn(async () => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <ConversationList
        {...baseProps}
        conversations={[conversation, second]}
        onRename={onRename}
        onDelete={onDelete}
      />,
    );

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search loaded chats" }),
      "hardware",
    );
    expect(screen.queryByRole("button", { name: /Local chat/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hardware plan/ })).toBeVisible();

    const hardware = screen.getByRole("group", { name: "Hardware plan" });
    await userEvent.click(within(hardware).getByRole("button", { name: "Rename conversation" }));
    const title = screen.getByRole("textbox", { name: "Conversation title" });
    await userEvent.clear(title);
    await userEvent.type(title, "GPU roadmap");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onRename).toHaveBeenCalledWith(second.id, "GPU roadmap");

    await userEvent.click(within(hardware).getByRole("button", { name: "Delete conversation" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(onDelete).toHaveBeenCalledWith(second.id);
  });

  it("sorts pinned chats and exposes owner pin, archive, and restore actions", async () => {
    const pinned = {
      ...conversation,
      id: "55555555-5555-4555-8555-555555555555",
      title: "Pinned plan",
      is_pinned: true,
    };
    const archived = {
      ...conversation,
      id: "66666666-6666-4666-8666-666666666666",
      title: "Archived plan",
      is_archived: true,
    };
    const onUpdateState = vi.fn(async () => undefined);
    const onShowArchivedChange = vi.fn();
    render(
      <ConversationList
        {...baseProps}
        conversations={[conversation, archived, pinned]}
        showArchived
        onUpdateState={onUpdateState}
        onShowArchivedChange={onShowArchivedChange}
      />,
    );

    const items = screen.getAllByRole("listitem");
    expect(within(items[0]!).getByText("Pinned plan")).toBeVisible();
    expect(within(items[2]!).getByText("Archived plan")).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", { name: "Show archived" }));
    expect(onShowArchivedChange).toHaveBeenCalledWith(false);
    await userEvent.click(screen.getByRole("button", { name: "Unpin conversation" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Archive conversation" })[0]!);
    await userEvent.click(screen.getByRole("button", { name: "Restore conversation" }));
    expect(onUpdateState).toHaveBeenCalledWith(pinned.id, { is_pinned: false });
    expect(onUpdateState).toHaveBeenCalledWith(expect.any(String), { is_archived: true });
    expect(onUpdateState).toHaveBeenCalledWith(archived.id, { is_archived: false });
  });
});
