"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ChatMessage, GroundingInfo } from "@/lib/types";
import { EvidenceSources, citedText } from "./EvidenceSources";

interface TutorChatProps {
  userId: string;
  sessionId: string;
  nodeId: string | null;
  history: ChatMessage[];
  sessionKey: unknown[]; // TanStack query key to invalidate after a turn persists
  contextLabel?: string; // the current lesson the tutor is grounded in (shown in the header)
}

// AI Teaching Assistant (P3 #10): a floating, right-docked, streaming chat grounded in the open lesson
// + the learner's uploaded material. Kept mounted (history/stream survive open/close).
export function TutorChat({
  userId,
  sessionId,
  nodeId,
  history,
  sessionKey,
  contextLabel,
}: TutorChatProps) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Announced to screen readers only (visible text is the streamed bubble itself, which updates too
  // fast/often to announce token-by-token). One announcement when the reply starts, one when it's
  // ready to read — never per-token, so it never spams.
  const [announcement, setAnnouncement] = useState("");

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setPendingUser(message);
    setStreaming("");
    setBusy(true);
    setAnnouncement("Tutor is responding…");

    try {
    await api.streamChat(userId, sessionId, nodeId, message, {
      onToken: (t) => setStreaming((s) => s + t),
      onError: (m) => {
        setError(m);
        setInput(message);
        qc.invalidateQueries({ queryKey: sessionKey });
        setAnnouncement("");
        setBusy(false);
        setPendingUser(null);
        setStreaming("");
      },
      onDone: () => {
        // Announce that the reply is ready, not the reply text itself — the text is already readable
        // as the streamed bubble above, and again in `bubbles` after the refetch below; duplicating it
        // into the live region would mean a screen reader user hits it twice while browsing back.
        setAnnouncement("Tutor replied.");
        setBusy(false);
        setPendingUser(null);
        setStreaming("");
        qc.invalidateQueries({ queryKey: sessionKey }); // persisted history is authoritative
      },
    });
    } catch {
      setError("The connection was interrupted. Your question is kept below; check saved history before retrying.");
      setInput(message);
      setBusy(false);
      setPendingUser(null);
      setStreaming("");
      setAnnouncement("");
      qc.invalidateQueries({ queryKey: sessionKey });
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="accent-ring fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full px-5 py-3 font-medium text-ink-900 shadow-lg transition-transform hover:-translate-y-0.5"
        style={{ background: "var(--color-gold)" }}
      >
        <span aria-hidden>💬</span> Ask the tutor
      </button>
    );
  }

  const bubbles: ChatMessage[] = [...history];
  if (pendingUser) bubbles.push({ role: "user", content: pendingUser });

  return (
    <aside
      className="surface fixed right-4 top-20 bottom-4 z-50 flex w-[380px] flex-col p-4 shadow-2xl max-sm:inset-x-2 max-sm:w-auto"
      role="dialog"
      aria-label="Teaching assistant"
    >
      {/* Screen-reader-only: the streamed reply is readable as normal DOM content once it lands, but
          nothing tells a screen reader user a reply is coming or done without this. Polite + atomic so
          it never interrupts, and it only changes twice per turn (start, done) — never per token. */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>

      <header className="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-text-strong">Teaching assistant</h2>
          <p className="truncate text-xs text-text-muted">
            Asking about: <span className="text-text">{contextLabel || "your learning path"}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close chat"
          className="accent-ring rounded-lg px-2 py-1 text-text-muted hover:text-text"
        >
          ✕
        </button>
      </header>

      <div className="mt-3 flex flex-1 flex-col gap-3 overflow-y-auto pr-1">
        {bubbles.length === 0 && !streaming && (
          <p className="text-sm text-text-muted">Ask anything about this lesson.</p>
        )}
        {bubbles.map((m, i) => (
          <Bubble key={i} role={m.role} content={m.content} grounding={m.grounding} />
        ))}
        {streaming && <Bubble role="assistant" content={streaming} />}
        {busy && !streaming && <p className="text-sm text-text-muted">Thinking…</p>}
      </div>

      {error && (
        <p role="alert" className="mt-2 text-sm" style={{ color: "var(--color-review)" }}>
          ⚠️ {error}
        </p>
      )}

      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
          placeholder="Ask a question…"
          aria-label="Message the teaching assistant"
          className="accent-ring flex-1 rounded-xl border border-white/10 bg-ink-850 px-3 py-2.5 text-text-strong placeholder:text-text-muted/60 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="accent-ring rounded-xl px-4 py-2.5 font-medium text-ink-900 disabled:opacity-50"
          style={{ background: "var(--color-gold)" }}
        >
          Send
        </button>
      </form>
    </aside>
  );
}

function Bubble({ role, content, grounding }: { role: "user" | "assistant"; content: string; grounding?: GroundingInfo | null }) {
  const isUser = role === "user";
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className="max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-sm"
        style={{
          background: isUser ? "var(--color-gold)" : "rgba(255,255,255,0.05)",
          color: isUser ? "var(--color-ink-900, #0b0b0c)" : "var(--color-text)",
          border: isUser ? "none" : "1px solid rgba(255,255,255,0.1)",
        }}
      >
        <p>{isUser ? content : citedText(content, grounding)}</p>
        {!isUser && grounding && <EvidenceSources evidence={grounding} />}
      </div>
    </div>
  );
}
