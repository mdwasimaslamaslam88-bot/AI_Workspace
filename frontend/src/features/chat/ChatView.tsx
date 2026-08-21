import { type FormEvent, useState } from "react";

import type {
  ConversationCreateRequest,
  ConversationSummary,
  Message,
} from "../../api/contracts";

export interface SafeNotice {
  message: string;
  status: number | null;
  requestId: string | null;
}

interface ChatViewProps {
  conversation: ConversationSummary | null;
  creatingNew: boolean;
  canGenerate: boolean;
  messages: Message[];
  nextCursor: number | null;
  loadingMessages: boolean;
  loadingMoreMessages: boolean;
  creatingConversation: boolean;
  generating: boolean;
  notice: SafeNotice | null;
  onCreateConversation: (request: ConversationCreateRequest) => Promise<void>;
  onCancelNew: () => void;
  onGenerate: (userMessage?: string) => Promise<void>;
  onCancelGeneration: () => void;
  onLoadMoreMessages: () => void;
  onReloadMessages: () => void;
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}

function NewConversationView({
  canGenerate,
  creating,
  notice,
  onCreate,
  onCancel,
}: {
  canGenerate: boolean;
  creating: boolean;
  notice: SafeNotice | null;
  onCreate: (request: ConversationCreateRequest) => Promise<void>;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [initialMessage, setInitialMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!initialMessage.trim() || !canGenerate) return;
    const request: ConversationCreateRequest = {
      initial_message: initialMessage,
    };
    if (title.trim()) request.title = title;
    if (systemPrompt.trim()) request.system_prompt = systemPrompt;
    await onCreate(request);
  }

  return (
    <section className="new-conversation" aria-labelledby="new-conversation-title">
      <div className="chat-heading">
        <div>
          <p className="eyebrow">New conversation</p>
          <h2 id="new-conversation-title">Start with a prompt</h2>
        </div>
        <button className="button button-quiet" onClick={onCancel}>Cancel</button>
      </div>
      <form className="new-conversation-form" onSubmit={(event) => void submit(event)}>
        <div>
          <label htmlFor="conversation-title">Title <span>(optional)</span></label>
          <input
            id="conversation-title"
            value={title}
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor="system-prompt">System prompt <span>(optional)</span></label>
          <textarea
            id="system-prompt"
            value={systemPrompt}
            maxLength={100000}
            rows={4}
            onChange={(event) => setSystemPrompt(event.target.value)}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor="initial-message">Your first message</label>
          <textarea
            id="initial-message"
            value={initialMessage}
            maxLength={100000}
            rows={7}
            required
            autoFocus
            onChange={(event) => setInitialMessage(event.target.value)}
            disabled={creating}
          />
        </div>
        {notice !== null && (
          <div className="notice notice-error" role="alert">
            <p>{notice.message}</p>
            <div className="diagnostics">
              {notice.status !== null && <span>HTTP {notice.status}</span>}
              {notice.requestId !== null && <span>Request {notice.requestId}</span>}
            </div>
          </div>
        )}
        {!canGenerate && (
          <p className="notice notice-error" role="alert">
            Select an available local text model before starting.
          </p>
        )}
        <button
          className="button button-primary"
          disabled={creating || !canGenerate || !initialMessage.trim()}
        >
          {creating ? "Creating…" : "Create and generate"}
        </button>
      </form>
    </section>
  );
}

export function ChatView({
  conversation,
  creatingNew,
  canGenerate,
  messages,
  nextCursor,
  loadingMessages,
  loadingMoreMessages,
  creatingConversation,
  generating,
  notice,
  onCreateConversation,
  onCancelNew,
  onGenerate,
  onCancelGeneration,
  onLoadMoreMessages,
  onReloadMessages,
}: ChatViewProps) {
  const [draft, setDraft] = useState("");

  if (creatingNew) {
    return (
      <NewConversationView
        canGenerate={canGenerate}
        creating={creatingConversation}
        notice={notice}
        onCreate={onCreateConversation}
        onCancel={onCancelNew}
      />
    );
  }

  if (conversation === null) {
    return (
      <section className="empty-chat">
        <p className="eyebrow">Ready when you are</p>
        <h2>Choose a conversation or start a new one.</h2>
        <p className="muted">
          Your backend remains the source of truth for identity, history, and
          local model execution.
        </p>
      </section>
    );
  }

  const lastMessage = messages.at(-1);
  const canRetryResponse = lastMessage?.role === "user";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim() || generating || !canGenerate) return;
    const message = draft;
    setDraft("");
    await onGenerate(message);
  }

  return (
    <section className="chat-view" aria-labelledby="conversation-heading">
      <header className="chat-heading">
        <div>
          <p className="eyebrow">Conversation</p>
          <h2 id="conversation-heading">
            {conversation.title ?? `Conversation ${conversation.id.slice(0, 8)}`}
          </h2>
        </div>
        <button
          className="button button-quiet"
          onClick={onReloadMessages}
          disabled={loadingMessages || generating}
        >
          Reload
        </button>
      </header>

      {notice !== null && (
        <div className="notice notice-error" role="alert">
          <p>{notice.message}</p>
          <div className="diagnostics">
            {notice.status !== null && <span>HTTP {notice.status}</span>}
            {notice.requestId !== null && <span>Request {notice.requestId}</span>}
          </div>
        </div>
      )}

      <div className="message-region" aria-live="polite" aria-busy={loadingMessages}>
        {loadingMessages && <p className="muted" role="status">Loading history…</p>}
        {!loadingMessages && messages.length === 0 && (
          <p className="empty-copy">This conversation has no messages.</p>
        )}
        <ol className="message-list">
          {messages.map((message) => (
            <li className={`message message-${message.role}`} key={message.id}>
              <div className="message-meta">
                <strong>{message.role}</strong>
                <time dateTime={message.created_at}>
                  {formatTimestamp(message.created_at)}
                </time>
              </div>
              <p>{message.content}</p>
            </li>
          ))}
        </ol>
        {nextCursor !== null && (
          <button
            className="button button-secondary load-messages"
            onClick={onLoadMoreMessages}
            disabled={loadingMoreMessages || generating}
          >
            {loadingMoreMessages ? "Loading…" : "Load more messages"}
          </button>
        )}
      </div>

      {canRetryResponse && !generating && (
        <button
          className="button button-secondary retry-response"
          onClick={() => void onGenerate()}
          disabled={!canGenerate}
        >
          Generate response to last message
        </button>
      )}

      <form className="composer" onSubmit={(event) => void submit(event)}>
        <label className="sr-only" htmlFor="chat-prompt">Message</label>
        <textarea
          id="chat-prompt"
          rows={3}
          maxLength={100000}
          value={draft}
          placeholder="Message your local AI"
          onChange={(event) => setDraft(event.target.value)}
          disabled={generating}
        />
        <div className="composer-actions">
          {generating ? (
            <>
              <span role="status">Generating a persisted response…</span>
              <button
                type="button"
                className="button button-secondary"
                onClick={onCancelGeneration}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              className="button button-primary"
              disabled={!canGenerate || !draft.trim()}
            >
              Send
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
