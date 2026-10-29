"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AssessmentQuiz } from "@/components/AssessmentQuiz";
import { AssessmentResults } from "@/components/AssessmentResults";
import { GradeResult } from "@/components/GradeResult";
import { LessonPanel } from "@/components/LessonPanel";
import { LessonSkeleton } from "@/components/LessonSkeleton";
import { PracticeHistory } from "@/components/PracticeHistory";
import { SkillGraph } from "@/components/SkillGraph";
import { TutorChat } from "@/components/TutorChat";
import { EvidenceSources } from "@/components/EvidenceSources";
import { LockedError, api } from "@/lib/api";
import { completeNodeIds, incompletePrereqLabels } from "@/lib/status";
import type { GradeResult as GradeResultT, SessionResponse } from "@/lib/types";
import { useUser } from "@/lib/useUser";

export default function SessionPage() {
  const { userId, ready } = useUser();
  const router = useRouter();
  const sessionId = String(useParams().sessionId);
  const qc = useQueryClient();
  const [lockMsg, setLockMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Only set when a grade response actually grew the graph — drives the SkillGraph camera cue so an
  // off-screen rewire (learner scrolled down at the editor) doesn't go unnoticed.
  const [rewireFocusIds, setRewireFocusIds] = useState<string[] | undefined>(undefined);
  // The grade the learner just got, held here rather than inside LessonPanel: a failing grade makes
  // the backend drop the cached lesson (so the next one targets the gaps), which unmounts the panel
  // and would take the score with it. Keyed by node so it can never render against another skill.
  const [gradeOutcome, setGradeOutcome] = useState<
    { nodeId: string; result: GradeResultT; newNodeCount: number } | null
  >(null);
  // Announced through a single polite live region below — the meaningful state changes a screen
  // reader user otherwise gets no signal for at all: lesson generation starting/finishing, a grade
  // coming back, and the graph growing new prerequisite nodes (all of which change the page's
  // structure without moving focus).
  const [liveMessage, setLiveMessage] = useState("");
  const [showResults, setShowResults] = useState(false);

  const key = ["session", userId, sessionId];

  const sessionQ = useQuery({
    queryKey: key,
    queryFn: () => api.getSession(userId!, sessionId),
    enabled: !!userId,
  });

  // Both mutations return the full, server-updated learning_session; write it straight into the
  // cache so the graph re-colors and any remedial node appears without a refetch.
  const writeBack = (res: SessionResponse) => qc.setQueryData(key, res);

  const open = useMutation({
    mutationFn: (nodeId: string) => api.openLesson(userId!, sessionId, nodeId),
    onSuccess: (res) => {
      writeBack(res);
      setErrorMsg(null);
      // A freshly opened lesson carries a different exercise, so the previous grade no longer applies.
      setGradeOutcome(null);
      setLiveMessage("Lesson ready.");
    },
    onError: (e) => {
      if (e instanceof LockedError) setLockMsg(`Locked — first complete: ${e.pending.join(", ")}`);
      else setErrorMsg(e.message); // e.g. 503 generation rate-limited — show "try again", not a crash
    },
  });

  const grade = useMutation({
    mutationFn: ({ nodeId, solution }: { nodeId: string; solution: string }) =>
      api.grade(userId!, sessionId, nodeId, solution, sessionQ.data?.learning_session.lessons[nodeId]?.exercise?.revision),
    onSuccess: (res, vars) => {
      writeBack(res);
      setErrorMsg(null);
      if (res.grade_result) {
        setGradeOutcome({
          nodeId: vars.nodeId,
          result: res.grade_result,
          newNodeCount: res.new_node_ids?.length ?? 0,
        });
      }
      if (res.new_node_ids?.length) setRewireFocusIds(res.new_node_ids);
      if (res.grade_result?.harness_error) {
        setLiveMessage("Not graded — the auto-grading tests for this exercise are broken. Your progress is unchanged.");
      } else if (res.grade_result) {
        const score = Math.round(res.grade_result.score ?? 0);
        const grew = res.new_node_ids?.length ?? 0;
        setLiveMessage(
          `Graded: ${score}%.${
            grew ? ` ${grew} new prerequisite ${grew === 1 ? "skill" : "skills"} added to the map.` : ""
          }`,
        );
      }
    },
    onError: (e) => setErrorMsg(e.message),
  });

  const assess = useMutation({
    mutationFn: (answers: number[]) => api.gradeAssessment(userId!, sessionId, answers),
    onSuccess: (res) => {
      writeBack(res);
      if (res.learning_session.assessment?.results) {
        setShowResults(true);
      }
    },
    onError: (e) => setErrorMsg(e.message),
  });

  const flag = useMutation({
    mutationFn: (nodeId: string) => api.flagLesson(userId!, sessionId, nodeId),
    onSuccess: writeBack,
    onError: (e) => setErrorMsg(e.message),
  });

  if (ready && !userId) {
    router.push("/");
    return null;
  }
  if (sessionQ.isLoading) return <Center>Loading path…</Center>;
  if (sessionQ.isError || !sessionQ.data) return <Center>Couldn’t load this path.</Center>;

  const session = sessionQ.data.learning_session;
  const total = session.skill_graph.nodes.length;
  const complete = completeNodeIds(session.skill_graph, session.node_state).size;
  const selected = session.selected_node;
  const pendingAssessment = !!session.assessment && !session.assessment.submitted;

  return (
    <main className="mx-auto max-w-7xl px-6 py-10 md:px-10">
      {/* Mounted once, always present with a stable id — content generation (lesson compose), grading,
          and graph growth (new prerequisite nodes) all change the page structurally with no focus
          move, so a screen reader user otherwise gets no signal any of it happened. `polite` so it
          never interrupts; kept terse, one line, replaced (not appended) so it never spams. */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {liveMessage}
      </div>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <button onClick={() => router.push("/")} className="text-sm text-text-muted hover:text-text">
            ← All paths
          </button>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-text-strong">
            {session.summary ?? "Your learning path"}
          </h1>
        </div>
        <div className="surface px-6 py-3 text-right">
          <p className="text-3xl font-semibold" style={{ color: "var(--color-mastered)" }}>
            {complete}
            <span className="text-text-muted">/{total}</span>
          </p>
          <p className="text-xs uppercase tracking-wider text-text-muted">skills complete</p>
        </div>
      </header>

      {/* The Reviewer agent's verdict, surfaced. The pipeline serves a rejected graph anyway (the
          path is still completable), so the learner has to be told the plan may have a hole — but
          only when the verdict is explicitly false; null/absent means "no verdict", not "failed". */}
      {session.assessment?.submitted && session.assessment.results && <button className="accent-ring mt-4 rounded-lg border px-4 py-2" onClick={() => setShowResults(true)}>Review assessment</button>}
      {session.review_passed === false && <ReviewCaveat notes={session.review_notes} />}
      <EvidenceSources evidence={session.grounding} />

      {pendingAssessment && session.assessment ? (
        <section className="mt-8">
          {errorMsg && (
            <p
              className="mb-3 rounded-lg border px-4 py-2 text-sm"
              style={{ color: "var(--color-review)", borderColor: "var(--color-review)" }}
            >
              ⚠️ {errorMsg}
            </p>
          )}
          <AssessmentQuiz
            quiz={session.assessment.quiz}
            submitting={assess.isPending}
            onSubmit={(answers) => {
              setErrorMsg(null);
              assess.mutate(answers);
            }}
            onSkip={() => {
              setErrorMsg(null);
              assess.mutate(session.assessment!.quiz.questions.map(() => -1));
            }}
          />
        </section>
      ) : showResults && session.assessment ? (
        <section className="mt-8">
          <AssessmentResults 
            assessment={session.assessment}
            onContinue={() => setShowResults(false)}
          />
        </section>
      ) : (
        <>
      {/* Stacked, full-width: the graph is the map up top; the lesson composes below it. */}
      <section className="mt-8">
        <SkillGraph
          session={session}
          focusNodeIds={rewireFocusIds}
          onOpen={(nodeId, locked) => {
            setLockMsg(null);
            setErrorMsg(null);
            if (locked) {
              setLockMsg(
                `Locked — first complete: ${incompletePrereqLabels(session.skill_graph, session.node_state, nodeId).join(", ")}`,
              );
              return;
            }
            setLiveMessage("Composing lesson…");
            open.mutate(nodeId);
          }}
        />
        {lockMsg && (
          <p
            role="alert"
            className="mt-3 rounded-lg border px-4 py-2 text-sm"
            style={{ color: "var(--color-blocked)", borderColor: "var(--color-blocked)" }}
          >
            🔒 {lockMsg}
          </p>
        )}
        {errorMsg && (
          <p
            role="alert"
            className="mt-3 rounded-lg border px-4 py-2 text-sm"
            style={{ color: "var(--color-review)", borderColor: "var(--color-review)" }}
          >
            ⚠️ {errorMsg}
          </p>
        )}
        {errorMsg && <button className="accent-ring mt-2 rounded-lg border px-4 py-2" onClick={() => { void sessionQ.refetch(); setErrorMsg(null); }}>Reload saved progress</button>}
        {open.isPending && (
          <p className="mt-3 flex items-center gap-2 text-sm text-text-muted">
            <span className="lesson-skeleton-spinner" />
            Composing lesson…
          </p>
        )}
      </section>

      <section className="mt-8">
        {open.isPending ? (
          <LessonSkeleton />
        ) : gradeOutcome && gradeOutcome.nodeId === selected ? (
          // The grade just came back — show the result panel prominently.
          <GradedOutcome
            outcome={gradeOutcome}
            label={session.skill_graph.nodes.find((n) => n.id === gradeOutcome.nodeId)?.label}
            locked={!!session.node_state[gradeOutcome.nodeId]?.remediation_pending}
            weaknesses={session.node_state[gradeOutcome.nodeId]?.weaknesses ?? []}
          />
        ) : selected && session.lessons[selected] ? (
          <LessonPanel
            key={`${selected}:${session.lessons[selected]?.exercise?.revision ?? "legacy"}`}
            session={session}
            nodeId={selected}
            grading={grade.isPending}
            onGrade={(solution) => grade.mutate({ nodeId: selected, solution })}
            onFlag={() => flag.mutate(selected)}
            flagging={flag.isPending}
          />
        ) : (
          <div className="surface flex min-h-[160px] items-center justify-center p-8 text-center text-text-muted">
            Pick a skill on the graph to open its lesson.
          </div>
        )}
      </section>

      {selected && <PracticeHistory
        key={selected}
        events={session.events}
        nodeId={selected}
        label={session.skill_graph.nodes.find((node) => node.id === selected)?.label ?? selected}
      />}

      <TutorChat
        userId={userId!}
        sessionId={sessionId}
        nodeId={selected ?? null}
        history={session.chat ?? []}
        sessionKey={key}
        contextLabel={
          selected
            ? session.skill_graph.nodes.find((n) => n.id === selected)?.label
            : undefined
        }
      />
        </>
      )}
    </main>
  );
}

// An editor's note, not a failure state: a slim gold-edged strip under the header rather than a
// `.surface` card, so it reads as an annotation on the roadmap instead of competing with it.
// `<aside>` (complementary landmark), never role="alert" — nothing has *happened*, this is standing
// context present on load. `notes` is model-generated and untrusted, so it goes through JSX text
// interpolation only: no markdown, no dangerouslySetInnerHTML, nothing that could inject markup.
// Dismissal is in-memory (state lives here, so the page's early returns can't reorder hooks) — the
// note is slight enough that surviving a reload costs nothing.
function ReviewCaveat({ notes }: { notes?: string | null }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  const reason = notes?.trim();

  return (
    <aside
      aria-label="Note about this path"
      className="mt-6 flex items-start gap-4 rounded-lg border-l-2 py-3 pl-4 pr-3"
      style={{ borderColor: "var(--color-gold-dim)", background: "oklch(80% 0.12 85 / 0.05)" }}
    >
      <div className="flex-1">
        <p className="eyebrow">Reviewer’s note</p>
        <p className="mt-1.5 text-sm text-text-muted">
          {reason
            ? `This path is usable as-is, but our reviewer flagged a possible gap: ${reason}`
            : "This path is usable as-is, but our reviewer flagged a possible gap in its coverage."}{" "}
          Worth a second look if something feels missing as you work through it.
        </p>
      </div>
      <button
        onClick={() => setDismissed(true)}
        aria-label="Dismiss reviewer’s note"
        className="accent-ring rounded px-2 py-1 text-sm text-text-muted hover:text-text"
      >
        ✕
      </button>
    </aside>
  );
}

// Shown in place of the lesson panel after a grade is received, so the learner clearly sees what happened.
function GradedOutcome({
  outcome,
  label,
  locked,
  weaknesses,
}: {
  outcome: { result: GradeResultT; newNodeCount: number };
  label?: string;
  locked: boolean;
  weaknesses: string[];
}) {
  const score = Math.round(outcome.result.score ?? 0);
  const mastered = !outcome.result.harness_error && score >= 80;

  return (
    <article className="surface p-7 md:p-10">
      <p className="eyebrow mb-4">
        {outcome.result.harness_error ? "Not graded" : "Graded"}
        {label ? ` — ${label}` : ""}
      </p>

      <GradeResult result={outcome.result} />

      {weaknesses.length > 0 && (
        <div className="mt-6">
          <p className="text-sm text-text-muted">Gaps to work on:</p>
          <ul className="mt-2 list-disc pl-5 text-sm text-text">
            {weaknesses.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 border-t border-white/5 pt-5 text-sm text-text-muted">
        {mastered ? (
          <p className="font-medium" style={{ color: "var(--color-mastered)" }}>
            Awesome work! You've mastered this skill. Pick the next available skill on the map to continue your path.
          </p>
        ) : locked ? (
          <p>
            This skill is locked until its prerequisites are complete
            {outcome.newNodeCount > 0
              ? ` — ${outcome.newNodeCount} new prerequisite ${
                  outcome.newNodeCount === 1 ? "skill was" : "skills were"
                } added to the map above.`
              : "."}{" "}
            Work through those first; reopening this skill will then give you a new lesson aimed at the gaps.
          </p>
        ) : (
          <p>
            Reopen this skill on the map above for a new lesson aimed at the gaps.
            {outcome.newNodeCount > 0
              ? ` ${outcome.newNodeCount} new ${
                  outcome.newNodeCount === 1 ? "skill was" : "skills were"
                } added to the map.`
              : ""}
          </p>
        )}
      </div>
    </article>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-screen place-items-center px-6 text-text-muted">{children}</main>
  );
}
