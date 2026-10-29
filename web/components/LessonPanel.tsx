"use client";

import { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/tokyo-night-dark.css";
import { CodeEditor } from "./CodeEditor";
import { GradeResult } from "./GradeResult";
import { MermaidDiagram } from "./MermaidDiagram";
import { Sandbox } from "./Sandbox";
import { EvidenceSources, citedText } from "./EvidenceSources";
import type { LearningSession } from "@/lib/types";

// rehype-highlight runs before these component overrides and only colorizes languages it
// recognizes; `ignoreMissing` makes it skip the ```mermaid fence untouched (no such grammar), so
// this override still sees the raw diagram source as a plain string. Route mermaid fences to a
// real diagram renderer; everything else falls through to react-markdown's (now highlighted)
// default code rendering.
const rehypeHighlightPlugins: [typeof rehypeHighlight, { ignoreMissing: boolean }][] = [
  [rehypeHighlight, { ignoreMissing: true }],
];
const markdownComponents: Components = {
  code(props) {
    const { className, children } = props;
    if (/\blanguage-mermaid\b/.test(className ?? "")) {
      return <MermaidDiagram code={String(children).trim()} />;
    }
    return <code className={className}>{children}</code>;
  },
};

export function LessonPanel({
  session,
  nodeId,
  onGrade,
  grading,
  onFlag,
  flagging,
}: {
  session: LearningSession;
  nodeId: string;
  onGrade: (solution: string) => void;
  grading: boolean;
  onFlag?: () => void;
  flagging?: boolean;
}) {
  const lesson = session.lessons[nodeId];
  const [solution, setSolution] = useState("");

  if (!lesson) return null;
  const exercise = lesson.exercise;
  const isCoding = (exercise?.format ?? "coding_challenge") === "coding_challenge";
  const exerciseLanguage = exercise?.language ?? exercise?.grading_artifact?.language ?? "python";
  const nodeState = session.node_state[nodeId];
  // Only display grade result if the learner has actually submitted a solution attempt
  const lastFeedback = (nodeState?.attempts ?? 0) > 0 ? (nodeState?.last_feedback ?? null) : null;
  // Already flagged? Derived from the funnel timeline so it persists across reloads.
  const flagged = (session.events ?? []).some(
    (e) => e.stage === "content_flagged" && e.node_id === nodeId,
  );

  return (
    <article className="surface p-7 md:p-10">
      <p className="eyebrow mb-4">Lesson</p>

      {/* The lesson markdown leads with its own <h1>, so no separate panel title (avoids duplicate). */}
      <div className="prose prose-invert prose-lg lesson-prose max-w-none">
        {lesson.content ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={rehypeHighlightPlugins}
            components={markdownComponents}
          >
            {citedText(lesson.content, lesson.grounding)}
          </ReactMarkdown>
        ) : (
          <p className="text-text-muted">Lesson content could not be generated.</p>
        )}
      </div>

      <EvidenceSources evidence={lesson.grounding} />

      {/* Explicit content-quality signal (Gate-1): the direct "was this lesson wrong?" measure. */}
      {onFlag && (
        <div className="mt-6 flex items-center gap-3 border-t border-white/5 pt-5">
          {flagged ? (
            <p className="text-sm text-text-muted">Thanks — flagged for review.</p>
          ) : (
            <button
              onClick={onFlag}
              disabled={flagging}
              className="accent-ring rounded-lg border border-white/10 px-4 py-1.5 text-sm text-text-muted transition-colors hover:text-text disabled:opacity-50"
            >
              {flagging ? "Sending…" : "👎 This lesson was off"}
            </button>
          )}
        </div>
      )}

      {exercise?.statement && (
        <section className="mt-10 rounded-2xl border border-white/10 bg-ink-850/60 p-6 md:p-7">
          <div className="mb-4 flex items-center gap-3">
            <span
              className="h-6 w-1 rounded-full"
              style={{ background: "var(--color-progress)" }}
              aria-hidden
            />
            <p className="eyebrow" style={{ color: "var(--color-progress)" }}>
              Practice
            </p>
          </div>

          <div className="prose prose-invert lesson-prose max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{citedText(exercise.statement, exercise.grounding ?? lesson.grounding)}</ReactMarkdown>
          </div>
          <EvidenceSources evidence={exercise.grounding ?? lesson.grounding} />

          <div className="mt-6">
            {isCoding ? (
              <CodeEditor value={solution} onChange={setSolution} language={exerciseLanguage} />
            ) : (
              <textarea
                value={solution}
                onChange={(e) => setSolution(e.target.value)}
                placeholder="Your solution, response, or analysis…"
                aria-label="Your response"
                className="accent-ring h-48 w-full rounded-xl border border-white/10 bg-ink-900 p-4 text-text-strong"
              />
            )}
          </div>

          <button
            onClick={() => onGrade(solution)}
            disabled={grading || !solution.trim()}
            className="accent-ring mt-5 rounded-xl px-6 py-2.5 font-medium text-ink-900 transition-transform hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0"
            style={{ background: "var(--color-gold)" }}
          >
            {grading ? "Grading…" : "Grade my submission"}
          </button>

          {lastFeedback && <GradeResult result={lastFeedback} />}

          {isCoding && <Sandbox />}
        </section>
      )}
    </article>
  );
}
