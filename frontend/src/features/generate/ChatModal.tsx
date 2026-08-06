import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../api/types";
import { parseChatMessage } from "./chatMarkdown";

interface ChatModalProps {
  messages: ChatMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  isPending: boolean;
  isError: boolean;
  onUseAsPrompt: (text: string) => void;
  onClose: () => void;
}

// A modal rather than the inline fieldset this used to be -- a chat is a
// focused, potentially multi-turn conversation, and the inline panel left
// it cramped into whatever space was left below the prompt box.
export function ChatModal({
  messages,
  input,
  onInputChange,
  onSend,
  isPending,
  isError,
  onUseAsPrompt,
  onClose,
}: ChatModalProps) {
  const chatLogRef = useRef<HTMLDivElement>(null);

  // Keep the chat log scrolled to the latest message/typing indicator --
  // otherwise "AI is typing" feedback can end up below the fold, invisible.
  useEffect(() => {
    const el = chatLogRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isPending]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide chat-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Prompt chat"
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>Prompt chat</h2>

        <div className="chat-log" ref={chatLogRef}>
          {messages.map((m, i) => {
            if (m.role === "user") {
              return (
                <div key={i} className="chat-message chat-user">
                  <strong>You:</strong> {m.content}
                </div>
              );
            }
            const { text, finalPrompt } = parseChatMessage(m.content);
            return (
              <div key={i} className="chat-message chat-assistant">
                <strong>AI:</strong>
                {text && (
                  <div className="chat-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
                  </div>
                )}
                {finalPrompt && (
                  <div className="final-prompt-card">
                    <p className="hint">Suggested prompt:</p>
                    <pre className="final-prompt-text">{finalPrompt}</pre>
                    <button type="button" onClick={() => onUseAsPrompt(finalPrompt)}>
                      Use as AI-refined prompt
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {isPending && (
            <div className="chat-message chat-assistant chat-typing">
              <strong>AI:</strong>
              <span className="typing-dots" aria-label="AI is typing">
                <span></span>
                <span></span>
                <span></span>
              </span>
            </div>
          )}
        </div>
        {isError && <p className="error">Message failed to send. Try again.</p>}
        <div className="chat-input-row">
          <input
            type="text"
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            placeholder="Ask the AI to help draft your prompt…"
            disabled={isPending}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onSend();
              }
            }}
          />
          <button type="button" onClick={onSend} disabled={isPending || !input.trim()}>
            {isPending ? "Sending…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
