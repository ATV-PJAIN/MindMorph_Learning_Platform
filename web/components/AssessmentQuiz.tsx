"use client";

import { useState } from "react";
import type { AssessmentQuiz as Quiz } from "@/lib/types";

interface AssessmentQuizProps {
  quiz: Quiz;
  submitting: boolean;
  onSubmit: (answers: number[]) => void;
  onSkip: () => void;
}

// Onboarding diagnostic (P2 #8): answer to test out of skills you already know. Correct answers
// pre-seed mastered nodes on the graph.
export function AssessmentQuiz({ quiz, submitting, onSubmit, onSkip }: AssessmentQuizProps) {
  const [answers, setAnswers] = useState<number[]>(() => quiz.questions.map(() => -1));

  const [dontKnow, setDontKnow] = useState<Record<number, boolean>>({});

  const choose = (qi: number, oi: number) => {
    setDontKnow((prev) => ({ ...prev, [qi]: false }));
    setAnswers((prev) => prev.map((a, i) => (i === qi ? oi : a)));
  };

  const chooseDontKnow = (qi: number) => {
    setDontKnow((prev) => ({ ...prev, [qi]: true }));
    setAnswers((prev) => prev.map((a, i) => (i === qi ? -1 : a)));
  };

  return (
    <section className="surface p-6 md:p-8">
      <p className="eyebrow mb-2">Quick check</p>
      <h2 className="text-2xl font-semibold text-text-strong">Find your starting point</h2>
      <p className="mt-2 max-w-2xl text-sm text-text-muted">
        Answer what you can — correct answers give you a head-start so you start where it matters.
        If you haven&rsquo;t learned something yet, choose &ldquo;No idea&rdquo; or skip.
      </p>

      <ol className="mt-6 flex flex-col gap-6">
        {quiz.questions.map((q, qi) => {
          const hasOptionDontKnow = q.options.some((opt) =>
            /don'?t know|no idea|not sure/i.test(opt)
          );

          return (
            <li key={qi} className="rounded-xl border border-white/10 bg-ink-850 p-5">
              <p id={`quiz-q${qi}`} className="font-medium text-text-strong">
                <span className="text-text-muted">{qi + 1}.</span> {q.question}
              </p>
              <div className="mt-3 grid gap-2" role="group" aria-labelledby={`quiz-q${qi}`}>
                {q.options.map((opt, oi) => {
                  const active = !dontKnow[qi] && answers[qi] === oi;
                  return (
                    <button
                      key={oi}
                      type="button"
                      onClick={() => choose(qi, oi)}
                      aria-pressed={active}
                      className="accent-ring rounded-lg border px-4 py-2.5 text-left text-sm transition-colors"
                      style={{
                        borderColor: active ? "var(--color-gold)" : "rgba(255,255,255,0.1)",
                        color: active ? "var(--color-gold)" : "var(--color-text)",
                        background: active ? "oklch(80% 0.12 85 / 0.1)" : undefined,
                      }}
                    >
                      {opt}
                    </button>
                  );
                })}

                {!hasOptionDontKnow && (
                  <button
                    type="button"
                    onClick={() => chooseDontKnow(qi)}
                    aria-pressed={dontKnow[qi]}
                    className="accent-ring rounded-lg border px-4 py-2.5 text-left text-sm transition-colors"
                    style={{
                      borderColor: dontKnow[qi] ? "var(--color-gold)" : "rgba(255,255,255,0.08)",
                      color: dontKnow[qi] ? "var(--color-gold)" : "var(--color-text-muted)",
                      background: dontKnow[qi] ? "oklch(80% 0.12 85 / 0.1)" : "rgba(255,255,255,0.02)",
                    }}
                  >
                    🤷 I don&apos;t know / No idea
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          disabled={submitting}
          onClick={() => onSubmit(answers)}
          className="accent-ring rounded-xl px-5 py-3 font-medium text-ink-900 disabled:opacity-60"
          style={{ background: "var(--color-gold)" }}
        >
          {submitting ? "Scoring…" : "Submit answers"}
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={onSkip}
          className="text-sm text-text-muted hover:text-text disabled:opacity-60"
        >
          Skip for now
        </button>
      </div>
    </section>
  );
}
