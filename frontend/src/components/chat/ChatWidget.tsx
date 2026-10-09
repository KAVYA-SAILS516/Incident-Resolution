import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api";
import type { ChatTurn } from "../../types";

const MAX_HISTORY = 10;

interface Message extends ChatTurn {
  referenced?: string[];
  error?: boolean;
}

function CloseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden focusable="false">
      <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function ChatBubbleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden focusable="false">
      <path d="M12 4C6.5 4 2 7.6 2 12c0 2.2 1.1 4.2 3 5.6V21l3.6-2.1c1 .3 2.2.4 3.4.4 5.5 0 10-3.6 10-8s-4.5-8-10-8z"
            fill="currentColor" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden focusable="false">
      <path d="M12 19V5M12 5l-6 6M12 5l6 6" fill="none" stroke="currentColor" strokeWidth="2.4"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Dashboard-wide chat with the Chat Agent (a third ADK agent, read-only over existing incident data).
 * Conversation is ephemeral: it lives only in this component's state, lost on refresh. Nothing here starts
 * the Investigation/Resolution Decision agents or the background workflow - it only answers questions. */
export function ChatWidget({ onOpenIncident }: { onOpenIncident: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const history = messages.slice(-MAX_HISTORY).map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setSending(true);
    try {
      const reply = await api.chat(text, history);
      setMessages((m) => [...m, { role: "assistant", content: reply.reply, referenced: reply.referenced_incidents }]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
      setMessages((m) => [...m, { role: "assistant", content: msg, error: true }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`chat-widget ${open ? "chat-open" : ""}`}>
      {open && (
        <div className="chat-panel card" role="dialog" aria-label="Chat with the incident assistant">
          <header className="chat-head">
            <div className="chat-head-title">
              <span className="chat-avatar" aria-hidden><ChatBubbleIcon /></span>
              <div>
                <strong>Incident Copilot</strong>
                <p className="muted small">Ask about any incident. Not persisted; nothing is executed.</p>
              </div>
            </div>
            <button type="button" className="chat-icon-btn" onClick={() => setOpen(false)} aria-label="Close chat panel">
              <CloseIcon />
            </button>
          </header>
          <div className="chat-messages" ref={listRef}>
            {messages.length === 0 && (
              <p className="muted small">
                Try "which P1 incidents are open?" or "tell me about INC-AC837A74".
              </p>
            )}
            {messages.map((m, n) => (
              <div key={n} className={`chat-msg chat-${m.role}${m.error ? " chat-error" : ""}`}>
                <div className="chat-bubble">{m.content}</div>
                {m.referenced && m.referenced.length > 0 && (
                  <div className="chat-refs">
                    {m.referenced.map((id) => (
                      <button key={id} type="button" className="chat-ref-chip" onClick={() => onOpenIncident(id)}>
                        {id}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {sending && (
              <div className="chat-msg chat-assistant">
                <div className="chat-bubble working" role="status"><span className="spinner" aria-hidden /> Thinking…</div>
              </div>
            )}
          </div>
          <form className="chat-input-row" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input type="text" value={input} onChange={(e) => setInput(e.target.value)} disabled={sending}
                   placeholder="Ask about an incident…" aria-label="Chat message" />
            <button type="submit" className="chat-send" disabled={sending || !input.trim()} aria-label="Send message">
              <SendIcon />
            </button>
          </form>
        </div>
      )}
      <button type="button" className="chat-bubble-toggle" onClick={() => setOpen((o) => !o)}
              aria-expanded={open} aria-label={open ? "Close chat" : "Open chat with the incident assistant"}>
        {open ? <CloseIcon size={22} /> : <ChatBubbleIcon />}
      </button>
    </div>
  );
}
