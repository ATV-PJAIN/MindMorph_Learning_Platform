"use client";

import type { GradeResult as GradeResultT } from "@/lib/types";

// Mastery threshold must match services/mastery.py MASTERY_THRESHOLD
const MASTERY_THRESHOLD = 80;

// Renders a persisted grade result (node_state.last_feedback), mirroring the Streamlit grade panel.
export function GradeResult({ result }: { result: GradeResultT }) {
  // The generated tests were broken, so nothing was measured. Say that plainly — showing 0% here
  // would blame the learner for our defect.
  if (result.harness_error) {
    return (
      <div className="mt-5 rounded-xl border border-white/10 bg-ink-850 p-4">
        <p className="text-sm font-semibold" style={{ color: "var(--color-progress)" }}>
          Not graded — the exercise&apos;s auto-grading tests are broken on our side.
        </p>
        <p className="mt-2 text-sm text-text-muted">
          Your submission was not scored and your progress is unchanged. The exercise&apos;s tests
          need to be repaired before this submission can be graded.
        </p>
        {(result.failures ?? []).map((f, i) => (
          <pre key={i} className="mt-2 overflow-x-auto rounded-lg bg-ink-900 p-3 text-xs text-text-muted">
            {f}
          </pre>
        ))}
      </div>
    );
  }

  const score = Math.round(result.score ?? 0);
  const isCoding = result.passed != null && result.total != null;
  const passed = isCoding && result.passed === result.total && (result.total ?? 0) > 0;
  const tone = passed || score >= 80 ? "var(--color-mastered)" : score >= 50 ? "var(--color-progress)" : "var(--color-review)";
  const needsPractice = !result.harness_error && score < MASTERY_THRESHOLD;

  return (
    <div className="mt-5 rounded-xl border border-white/10 bg-ink-850 p-4">
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-semibold" style={{ color: tone }}>
          {score}%
        </span>
        {isCoding && (
          <span className="text-sm text-text-muted">
            {result.passed}/{result.total} tests passed
          </span>
        )}
      </div>

      {/* Task 3: Bold inline banner for below-mastery scores — immediately visible, no scroll jank.
          The banner explains WHY the learner can't advance and WHAT to do next. */}
      {needsPractice && (
        <div
          className="mt-4 rounded-lg border px-4 py-3"
          style={{
            borderColor: tone,
            background: score >= 50
              ? "oklch(80% 0.12 85 / 0.08)"   /* warm amber tint for 50–79% */
              : "oklch(65% 0.20 25 / 0.08)",   /* red tint for below 50% */
          }}
        >
          <p className="text-sm font-semibold" style={{ color: tone }}>
            🔄 Keep practicing to unlock the next lesson!
          </p>
          <p className="mt-1 text-xs text-text-muted">
            You need {MASTERY_THRESHOLD}% to master this skill. Review the feedback below and try again.
          </p>
        </div>
      )}

      {result.timed_out && (
        <p className="mt-2 text-sm" style={{ color: "var(--color-progress)" }}>
          Execution timed out (possible infinite loop).
        </p>
      )}

      {(result.failures ?? []).map((f, i) => (
        <pre key={i} className="mt-2 overflow-x-auto rounded-lg bg-ink-900 p-3 text-xs text-text">
          {f}
        </pre>
      ))}

      {(result.per_criterion ?? []).map((line, i) => (
        <p key={i} className="mt-1 text-sm text-text">
          • {line}
        </p>
      ))}

      {result.feedback && <p className="mt-3 text-sm text-text">{result.feedback}</p>}
    </div>
  );
}
