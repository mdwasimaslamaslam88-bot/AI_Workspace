import type {
  ConversationCursor,
  ConversationSummary,
} from "../../api/contracts";

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
  onReload,
  onLoadMore,
  onLogout,
}: ConversationListProps) {
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
        <ul className="conversation-list">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                className={
                  conversation.id === selectedId
                    ? "conversation-item selected"
                    : "conversation-item"
                }
                aria-current={conversation.id === selectedId ? "page" : undefined}
                onClick={() => onSelect(conversation)}
                disabled={disabled}
              >
                <strong>{conversationName(conversation)}</strong>
                <span>{formatUpdatedAt(conversation.updated_at)}</span>
              </button>
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
