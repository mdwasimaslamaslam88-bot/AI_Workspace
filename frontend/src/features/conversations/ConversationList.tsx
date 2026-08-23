import type {
  ConversationCursor,
  ConversationSummary,
} from "../../api/contracts";
import { useMemo, useState } from "react";

interface ConversationListProps {
  conversations: ConversationSummary[];
  userId: string;
  selectedId: string | null;
  nextCursor: ConversationCursor | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  disabled?: boolean;
  onCreate: () => void;
  onSelect: (conversation: ConversationSummary) => void;
  onRename: (conversationId: string, title: string | null) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
  onReload: () => void;
  onLoadMore: () => void;
  onLogout: () => void;
}

function conversationName(conversation: ConversationSummary): string {
  return conversation.title ?? `Conversation ${conversation.id.slice(0, 8)}`;
}

function formatUpdatedAt(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.valueOf())
    ? "Updated recently"
    : `Updated ${date.toLocaleString()}`;
}

export function ConversationList({
  conversations,
  userId,
  selectedId,
  nextCursor,
  loading,
  loadingMore,
  error,
  disabled = false,
  onCreate,
  onSelect,
  onRename,
  onDelete,
  onReload,
  onLoadMore,
  onLogout,
}: ConversationListProps) {
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [mutationId, setMutationId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const visibleConversations = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (normalized.length === 0) return conversations;
    return conversations.filter((conversation) =>
      conversationName(conversation).toLocaleLowerCase().includes(normalized),
    );
  }, [conversations, query]);

  async function saveRename(conversationId: string) {
    const normalized = draftTitle.trim();
    setMutationId(conversationId);
    setMutationError(null);
    try {
      await onRename(conversationId, normalized.length === 0 ? null : normalized);
      setEditingId(null);
      setDraftTitle("");
    } catch {
      setMutationError("The conversation could not be renamed.");
    } finally {
      setMutationId(null);
    }
  }

  async function removeConversation(conversation: ConversationSummary) {
    if (!window.confirm(`Delete ${conversationName(conversation)} and its owned history?`)) {
      return;
    }
    setMutationId(conversation.id);
    setMutationError(null);
    try {
      await onDelete(conversation.id);
      if (editingId === conversation.id) setEditingId(null);
    } catch {
      setMutationError("The conversation could not be deleted.");
    } finally {
      setMutationId(null);
    }
  }

  return (
    <aside className="sidebar" aria-label="Conversation navigation">
      <div className="sidebar-header">
        <div>
          <p className="eyebrow">Private Personal AI</p>
          <h1>WORK STATION</h1>
        </div>
        <button className="button button-quiet" onClick={onLogout}>Logout</button>
      </div>
      <p className="sidebar-user" title={userId}>
        <span>Authenticated user</span>
        <strong>{userId}</strong>
      </p>

      <button
        className="button button-primary full-width"
        onClick={onCreate}
        disabled={disabled}
      >
        New conversation
      </button>

      <label className="conversation-search">
        <span>Search loaded chats</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search conversations"
        />
      </label>

      <nav className="conversation-nav" aria-label="Conversations">
        {loading && <p className="muted" role="status">Loading conversations…</p>}
        {!loading && error !== null && (
          <div className="notice notice-error" role="alert">
            <p>{error}</p>
            <button className="button button-quiet" onClick={onReload}>Retry</button>
          </div>
        )}
        {!loading && error === null && conversations.length === 0 && (
          <p className="empty-copy">No conversations yet.</p>
        )}
        {!loading && error === null && conversations.length > 0 && visibleConversations.length === 0 && (
          <p className="empty-copy">No loaded conversations match that search.</p>
        )}
        {mutationError !== null && (
          <p className="notice notice-error" role="alert">{mutationError}</p>
        )}
        <ul className="conversation-list">
          {visibleConversations.map((conversation) => (
            <li key={conversation.id}>
              <div
                className="conversation-entry"
                role="group"
                aria-label={conversationName(conversation)}
              >
                {editingId === conversation.id ? (
                  <form
                    className="conversation-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveRename(conversation.id);
                    }}
                  >
                    <label>
                      <span className="visually-hidden">Conversation title</span>
                      <input
                        autoFocus
                        maxLength={255}
                        value={draftTitle}
                        onChange={(event) => setDraftTitle(event.target.value)}
                      />
                    </label>
                    <button className="button button-quiet" disabled={mutationId !== null}>Save</button>
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={() => setEditingId(null)}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <button
                    className={
                      conversation.id === selectedId
                        ? "conversation-item selected"
                        : "conversation-item"
                    }
                    aria-current={conversation.id === selectedId ? "page" : undefined}
                    onClick={() => onSelect(conversation)}
                    disabled={disabled || mutationId !== null}
                  >
                    <strong>{conversationName(conversation)}</strong>
                    <span>{formatUpdatedAt(conversation.updated_at)}</span>
                  </button>
                )}
                <div className="conversation-actions">
                  <button
                    type="button"
                    className="button button-quiet"
                    aria-label="Rename conversation"
                    disabled={disabled || mutationId !== null}
                    onClick={() => {
                      setEditingId(conversation.id);
                      setDraftTitle(conversation.title ?? "");
                      setMutationError(null);
                    }}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    className="button button-quiet danger-action"
                    aria-label="Delete conversation"
                    disabled={disabled || mutationId !== null}
                    onClick={() => void removeConversation(conversation)}
                  >
                    {mutationId === conversation.id ? "Working…" : "Delete"}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
        {nextCursor !== null && (
          <button
            className="button button-secondary full-width"
            onClick={onLoadMore}
            disabled={loadingMore || disabled}
          >
            {loadingMore ? "Loading…" : "Load more conversations"}
          </button>
        )}
      </nav>
    </aside>
  );
}
