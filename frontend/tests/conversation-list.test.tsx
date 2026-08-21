import { render, screen } from "@testing-library/react";
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
  onCreate: vi.fn(),
  onSelect: vi.fn(),
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
});
