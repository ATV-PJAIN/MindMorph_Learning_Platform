// Typed client for the MindMorph FastAPI service. All calls are thin wrappers over fetch; the API
// holds no per-request state, so the whole learning_session is passed back and forth as a blob.

import type {
  IngestResponse,
  LearningSession,
  SessionMeta,
  SessionResponse,
  StartSessionResponse,
  FollowUpResponse,
  GroundingInfo,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class LockedError extends Error {
  pending: string[];
  constructor(pending: string[]) {
    super("Node is locked");
    this.name = "LockedError";
    this.pending = pending;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    if (res.status === 409 && detail?.error === "locked") throw new LockedError(detail?.pending ?? []);
    // 503 (and friends) carry a safe string message — surface it verbatim for the UI to show.
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status}).`);
  }
  return res.json() as Promise<T>;
}

// Shared SSE frame reader: fetch + reader, not EventSource (which is GET-only). Buffers across reads
// because reader chunks don't align to "\n\n" frame boundaries. `onFrame` returns `true` to stop early
// (a terminal/error frame); otherwise the loop drains the whole stream.
async function readSseFrames(res: Response, onFrame: (evt: any) => boolean | void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.replace(/^data: /, "").trim();
      if (!line) continue;
      if (onFrame(JSON.parse(line))) return;
    }
  }
}

export const api = {
  getFollowUp(query: string) {
    return req<FollowUpResponse>("/sessions/followup", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  },

  createSession(userId: string, query: string, formatType = "B") {
    return req<StartSessionResponse>("/sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, query, format_type: formatType }),
    });
  },

  listSessions(userId: string) {
    return req<SessionMeta[]>(`/sessions/${encodeURIComponent(userId)}`);
  },

  getSession(userId: string, sessionId: string) {
    return req<SessionResponse>(
      `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`,
    );
  },

  openLesson(userId: string, sessionId: string, nodeId: string) {
    return req<SessionResponse>(
      `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/lessons/${encodeURIComponent(nodeId)}`,
      { method: "POST" },
    );
  },

  grade(userId: string, sessionId: string, nodeId: string, solution: string, exerciseRevision?: string) {
    const q = new URLSearchParams({ node_id: nodeId }).toString();
    return req<SessionResponse>(
      `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/grade?${q}`,
      { method: "POST", body: JSON.stringify({ solution, exercise_revision: exerciseRevision }) },
    );
  },

  flagLesson(userId: string, sessionId: string, nodeId: string, reason?: string) {
    return req<SessionResponse>(
      `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/lessons/${encodeURIComponent(nodeId)}/flag`,
      { method: "POST", body: JSON.stringify({ reason: reason ?? null }) },
    );
  },

  gradeAssessment(userId: string, sessionId: string, answers: number[]) {
    return req<SessionResponse>(
      `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/assessment`,
      { method: "POST", body: JSON.stringify({ answers }) },
    );
  },

  // Streaming chat (SSE over POST). See readSseFrames above for the shared transport.
  async streamChat(
    userId: string,
    sessionId: string,
    nodeId: string | null,
    message: string,
    cb: { onToken: (t: string) => void; onDone: () => void; onError: (m: string) => void; onEvidence?: (e: GroundingInfo) => void },
  ): Promise<void> {
    const res = await fetch(
      `${BASE}/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/chat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, node_id: nodeId }),
      },
    );
    if (!res.ok || !res.body) {
      cb.onError(`Chat failed (${res.status}).`);
      return;
    }
    let finished = false;
    await readSseFrames(res, (evt: { token?: string; done?: boolean; error?: string; grounding?: GroundingInfo }) => {
      if (evt.grounding) cb.onEvidence?.(evt.grounding);
      if (evt.error) {
        finished = true;
        cb.onError(evt.error);
        return true;
      }
      if (evt.done) {
        finished = true;
        cb.onDone();
        return true;
      }
      if (evt.token) cb.onToken(evt.token);
    });
    if (!finished) cb.onDone();
  },

  // Streaming session create (SSE over POST): one "stage" frame per LangGraph node (scout, academic,
  // market, practical, consensus, reviewer, ...) as orchestration runs, then a "done" frame carrying
  // the same payload shape as createSession's response.
  async startSessionStream(
    userId: string,
    query: string,
    formatType: string,
    opts: {
      preferences?: Record<string, string>;
      primary_language?: string | null;
      scope?: string;
      domain?: string;
      onStage: (stage: string, label: string) => void;
      onDone: (res: StartSessionResponse) => void;
      onError: (m: string) => void;
    },
  ): Promise<void> {
    const res = await fetch(`${BASE}/sessions/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        user_id: userId, 
        query, 
        format_type: formatType,
        preferences: opts.preferences,
        primary_language: opts.primary_language,
        scope: opts.scope,
        domain: opts.domain,
      }),
    });
    if (!res.ok || !res.body) {
      opts.onError(`Request failed (${res.status}).`);
      return;
    }
    let finished = false;
    await readSseFrames(
      res,
      (evt: { stage?: string; label?: string; done?: boolean; session?: StartSessionResponse; error?: string }) => {
        if (evt.error) {
          finished = true;
          opts.onError(evt.error);
          return true;
        }
        if (evt.done) {
          finished = true;
          opts.onDone(evt.session as StartSessionResponse);
          return true;
        }
        if (evt.stage) opts.onStage(evt.stage, evt.label ?? evt.stage);
      },
    );
    if (!finished) opts.onError("Stream ended unexpectedly.");
  },

  // Multipart upload — must NOT set Content-Type (the browser sets the multipart boundary), so this
  // bypasses `req` (which forces application/json).
  async ingestKnowledge(userId: string, file: File): Promise<IngestResponse> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/users/${encodeURIComponent(userId)}/knowledge`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      const detail = body?.detail;
      throw new Error(typeof detail === "string" ? detail : `Upload failed (${res.status}).`);
    }
    return res.json() as Promise<IngestResponse>;
  },
};

export type { LearningSession };
