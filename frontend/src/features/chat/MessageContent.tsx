import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MessageContentProps {
  content: string;
  role: string;
}

function preserveRawHtmlAsText(content: string): string {
  return content.replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

export function MessageContent({ content, role }: MessageContentProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  async function copyMessage() {
    try {
      if (navigator.clipboard?.writeText === undefined) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(content);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  return (
    <div className="message-content">
      <div className="markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          skipHtml
          disallowedElements={["img"]}
          unwrapDisallowed
          components={{
            a: ({ children }) => (
              <span
                className="markdown-link-disabled"
                title="External links are disabled in model output"
              >
                {children}
              </span>
            ),
          }}
        >
          {preserveRawHtmlAsText(content)}
        </ReactMarkdown>
      </div>
      <div className="message-content-actions">
        <button
          type="button"
          className="button button-quiet"
          aria-label={`Copy ${role} message`}
          onClick={() => void copyMessage()}
        >
          {copyState === "copied" ? "Copied" : "Copy"}
        </button>
        {copyState === "failed" && (
          <span className="copy-failed" role="status">Clipboard unavailable</span>
        )}
      </div>
    </div>
  );
}
