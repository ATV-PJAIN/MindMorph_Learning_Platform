"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useUser } from "@/lib/useUser";
import { KnowledgeUpload } from "@/components/KnowledgeUpload";
import { FollowUpModal } from "@/components/FollowUpModal";
import { PathSummaryModal } from "@/components/PathSummaryModal";
import { EvidenceSources, citedText } from "@/components/EvidenceSources";
import type { FollowUpQuestion, LearningSession, StartSessionResponse } from "@/lib/types";

export default function HomePage() {
  const { userId, ready, signIn, signOut } = useUser();
  if (!ready) return <Shell><div className="text-text-muted">Loading…</div></Shell>;
  if (!userId) return <LoginGate onSignIn={signIn} />;
  return <Dashboard userId={userId} onSignOut={signOut} />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-16">{children}</main>
  );
}

function LoginGate({ onSignIn }: { onSignIn: (id: string) => void }) {
  const [email, setEmail] = useState("");
  return (
    <Shell>
      <div className="flex flex-1 flex-col justify-center">
        <p className="eyebrow mb-4">Adaptive Learning</p>
        <h1 className="max-w-2xl text-5xl font-semibold leading-[1.05] text-text-strong md:text-6xl">
          Click a skill. Learn it.{" "}
          <span style={{ color: "var(--color-gold)" }}>Prove it.</span>
        </h1>
        <p className="mt-5 max-w-xl text-lg text-text-muted">
          MindMorph maps what you want to learn into a skill graph, then adapts the path to how you
          actually perform.
        </p>
        <form
          className="mt-10 flex max-w-md gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            onSignIn(email);
          }}
        >
          <input
            type="email"
            required
            placeholder="you@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-label="Email"
            className="accent-ring flex-1 rounded-xl border border-white/10 bg-ink-800 px-4 py-3 text-text-strong placeholder:text-text-muted/60"
          />
          <button
            type="submit"
            className="accent-ring rounded-xl px-5 py-3 font-medium text-ink-900 transition-transform hover:-translate-y-0.5"
            style={{ background: "var(--color-gold)" }}
          >
            Enter
          </button>
        </form>
        <p className="mt-3 text-xs text-text-muted/70">No password — MVP identity only.</p>
      </div>
    </Shell>
  );
}

function Dashboard({ userId, onSignOut }: { userId: string; onSignOut: () => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [stageLabel, setStageLabel] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createNotice, setCreateNotice] = useState<string | null>(null);
  const [followUpQuestions, setFollowUpQuestions] = useState<FollowUpQuestion[]>([]);
  const [detectedLanguage, setDetectedLanguage] = useState<string | null>(null);
  const [detectedDomain, setDetectedDomain] = useState<string>("Learning");
  const [showFollowUp, setShowFollowUp] = useState(false);
  const [followUpFailed, setFollowUpFailed] = useState(false);
  const [directResponse, setDirectResponse] = useState<StartSessionResponse | null>(null);
  // Path summary modal state
  const [showSummary, setShowSummary] = useState(false);
  const [summarySession, setSummarySession] = useState<LearningSession | null>(null);
  const [summarySessionId, setSummarySessionId] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: ["sessions", userId],
    queryFn: () => api.listSessions(userId),
  });

  async function handleCreate() {
    if (!query.trim() || creating) return;
    setCreating(true);
    setCreateError(null);
    setCreateNotice(null);
    setFollowUpFailed(false);
    setDirectResponse(null);
    try {
      const followUp = await api.getFollowUp(query);
      setFollowUpQuestions(followUp.questions);
      setDetectedLanguage(followUp.detected_language);
      setDetectedDomain(followUp.detected_domain || "Learning");
      setShowFollowUp(followUp.questions.length > 0);
      setCreating(false);
      if (!followUp.questions.length) setFollowUpFailed(true);
    } catch (err) {
      setCreating(false);
      setFollowUpFailed(true);
      setDetectedLanguage(null);
      setDetectedDomain("Learning");
      setCreateError("Couldn't prepare questions. Retry or continue without them.");
    }
  }

  async function handleFollowUpSubmit(answers: Record<string, string>) {
    setShowFollowUp(false);
    setFollowUpFailed(false);
    setCreating(true);
    setStageLabel("Starting agents");
    setCreateError(null);
    
    // Extract scope if present, else fallback
    const scope = answers.scope || "foundation";

    try {
      await api.startSessionStream(userId, query, "B", {
        preferences: answers,
        primary_language: answers.language || answers.primary_language || detectedLanguage,
        scope: scope,
        domain: detectedDomain,
        onStage: (_stage, label) => setStageLabel(label),
        onDone: (res) => {
          setCreating(false);
          setStageLabel(null);
          qc.invalidateQueries({ queryKey: ["sessions", userId] });
          if (res.session_id && res.learning_session) {
            // Show summary card instead of immediately navigating
            setSummarySession(res.learning_session as LearningSession);
            setSummarySessionId(res.session_id);
            setShowSummary(true);
          } else if (res.session_id) {
            // Fallback: navigate directly if no learning_session in response
            router.push(`/session/${res.session_id}`);
          } else {
            setDirectResponse(res);
            setCreateNotice("Your answer is ready below.");
          }
        },
        onError: (msg) => {
          setCreating(false);
          setStageLabel(null);
          setCreateError(msg);
        },
      });
    } catch {
      setCreating(false);
      setStageLabel(null);
      setCreateError("Couldn't reach the learning service. Is the API running?");
    }
  }

  function handleExplorePath() {
    setShowSummary(false);
    if (summarySessionId) {
      router.push(`/session/${summarySessionId}`);
    }
    setSummarySession(null);
    setSummarySessionId(null);
  }

  return (
    <Shell>
      <header className="flex items-center justify-between">
        <p className="eyebrow">MindMorph</p>
        <button onClick={onSignOut} className="text-sm text-text-muted hover:text-text">
          {userId} · sign out
        </button>
      </header>

      <section className="surface mt-8 p-6">
        {/* Screen-reader-only progress: stageLabel is otherwise visible text alone, so a screen reader
            user gets nothing for the ~38s build. Stages arrive a few seconds apart (not token-rate), so
            each one is announced — polite + atomic keeps it from interrupting typing elsewhere. */}
        <div aria-live="polite" aria-atomic="true" className="sr-only">
          {creating ? stageLabel ?? "Starting agents" : ""}
        </div>
        <h2 className="text-xl font-semibold text-text-strong">Start a new path</h2>
        <form
          className="mt-4 flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            handleCreate();
          }}
        >
          <input
            placeholder="e.g. Learn Python for data science, Master guitar chords, Financial modeling..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="What do you want to learn?"
            className="accent-ring flex-1 rounded-xl border border-white/10 bg-ink-850 px-4 py-3 text-text-strong placeholder:text-text-muted/60"
          />
          <button
            type="submit"
            id="generate-path"
            disabled={creating}
            className="accent-ring rounded-xl px-5 py-3 font-medium text-ink-900 disabled:opacity-60"
            style={{ background: "var(--color-gold)" }}
          >
            {creating ? "Building…" : "Generate"}
          </button>
        </form>
        {createError && (
          <p role="alert" className="mt-3 text-sm" style={{ color: "var(--color-review)" }}>
            {createError}
          </p>
        )}
        {followUpFailed && !creating && <button type="button" className="accent-ring mt-3 rounded-lg border px-4 py-2" onClick={() => void handleFollowUpSubmit({})}>Continue without questions</button>}
        {createNotice && (
          <div role="status" className="mt-3">
            <p className="eyebrow mb-1">Answered directly</p>
            <p className="text-sm text-text-muted">{createNotice}</p>
          </div>
        )}
        {directResponse && <section className="mt-4 whitespace-pre-wrap" aria-label="Direct answer">{citedText(directResponse.final_content || directResponse.exercise?.statement || "No answer was generated. Please try again.", directResponse.grounding)}<EvidenceSources evidence={directResponse.grounding} /></section>}
        {creating && (
          <p className="mt-3 flex items-center gap-2 text-sm text-text-muted">
            <span
              className="h-3 w-3 animate-spin rounded-full border-2 border-transparent"
              style={{ borderTopColor: "var(--color-gold)", borderRightColor: "var(--color-gold)" }}
            />
            <span className="eyebrow mb-0">{stageLabel ?? "Starting agents"}</span>
          </p>
        )}
      </section>

      {showFollowUp && (
        <FollowUpModal
          questions={followUpQuestions}
          domain={detectedDomain}
          onSubmit={handleFollowUpSubmit}
          onCancel={() => setShowFollowUp(false)}
        />
      )}

      {showSummary && summarySession && (
        <PathSummaryModal
          session={summarySession}
          query={query}
          domain={detectedDomain}
          onExplore={handleExplorePath}
        />
      )}

      <KnowledgeUpload userId={userId} />

      {sessions.isError && <p role="alert">Could not load saved paths. <button onClick={() => void sessions.refetch()}>Retry saved paths</button></p>}
      <section className="mt-10">
        <h2 className="text-sm uppercase tracking-wider text-text-muted">Your paths</h2>
        <div className="mt-4 grid gap-3">
          {sessions.isLoading && <p className="text-text-muted">Loading…</p>}
          {sessions.data?.length === 0 && (
            <p className="text-text-muted">No paths yet — start one above.</p>
          )}
          {sessions.data?.map((s) => (
            <button
              key={s.session_id}
              onClick={() => router.push(`/session/${s.session_id}`)}
              className="surface accent-ring group flex items-center justify-between p-5 text-left transition-transform hover:-translate-y-0.5"
            >
              <span className="font-medium text-text-strong">{s.title || "Untitled path"}</span>
              <span className="text-sm text-text-muted group-hover:text-gold">Resume →</span>
            </button>
          ))}
        </div>
      </section>
    </Shell>
  );
}
