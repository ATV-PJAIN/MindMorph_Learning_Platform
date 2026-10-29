import { Assessment } from "@/lib/types";

interface AssessmentResultsProps {
  assessment: Assessment;
  onContinue: () => void;
}

export function AssessmentResults({ assessment, onContinue }: AssessmentResultsProps) {
  const { quiz, results } = assessment;

  if (!results) return null;

  const score = results.filter((r) => r.is_correct).length;
  const total = results.length;
  const percent = total > 0 ? Math.round((score / total) * 100) : 0;

  return (
    <section className="surface p-6 md:p-8 animate-fade-in">
      {/* Result summary card */}
      <div
        className="mb-8 rounded-2xl border border-white/10 p-6 md:p-7 text-center relative overflow-hidden"
        style={{
          background: "linear-gradient(135deg, oklch(80% 0.12 85 / 0.1), oklch(78% 0.14 165 / 0.06))",
        }}
      >
        <p className="eyebrow mb-2">Diagnostic Assessment Complete</p>
        <div className="flex items-center justify-center gap-3 my-3">
          <span className="text-4xl">{score > 0 ? "🎯" : "🌱"}</span>
          <h2 className="text-3xl font-bold text-text-strong">
            {score} of {total} Correct
          </h2>
          <span
            className="rounded-full px-3 py-1 text-xs font-semibold"
            style={{
              background: score > 0 ? "var(--color-mastered)" : "var(--color-gold)",
              color: "#0f172a",
            }}
          >
            {percent}%
          </span>
        </div>

        <p className="max-w-xl mx-auto text-sm text-text-muted mt-2 leading-relaxed">
          {score > 0
            ? `Great job! You already know ${score} foundational skill${score === 1 ? "" : "s"}. Correct answers give you a head-start. Complete each skill's exercise to demonstrate mastery.`
            : "Perfect! We've prepared a complete, step-by-step roadmap tailored for you starting right from the fundamentals."}
        </p>

        <div className="mt-6 flex justify-center">
          <button
            onClick={onContinue}
            className="accent-ring rounded-xl px-7 py-3.5 font-medium text-ink-900 shadow-lg shadow-gold/20 hover:-translate-y-0.5 transition-all flex items-center gap-2"
            style={{ background: "var(--color-gold)" }}
          >
            <span>Move to Roadmap</span>
            <span className="text-lg">→</span>
          </button>
        </div>
      </div>

      {/* Answer breakdown header */}
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-base font-semibold text-text-strong">Question Review</h3>
        <span className="text-xs text-text-muted">
          {score} correct · {total - score} to learn
        </span>
      </div>

      {/* Questions review list */}
      <div className="space-y-4">
        {quiz.questions.map((q, qi) => {
          const res = q.id ? results.find((r) => r.question_id === q.id) : results[qi];
          if (!res) return null;

          return (
            <div
              key={qi}
              className={`rounded-xl border p-5 transition-colors ${
                res.is_correct
                  ? "border-green-500/25 bg-green-500/5"
                  : "border-white/10 bg-ink-850/60"
              }`}
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <p className="font-medium text-text-strong">
                  <span className="text-text-muted">{qi + 1}.</span> {q.question}
                </p>
                <span
                  className={`text-xs px-2.5 py-1 rounded-full font-medium shrink-0 ${
                    res.is_correct
                      ? "bg-green-500/20 text-green-300"
                      : "bg-white/10 text-text-muted"
                  }`}
                >
                  {res.is_correct ? "✓ Recognized" : "To Learn"}
                </span>
              </div>

              <div className="space-y-1.5 mb-3">
                {q.options.map((opt, oi) => {
                  let indicator = "";
                  let classes = "text-text-muted/70";
                  if (oi === res.correct_index) {
                    indicator = "✓ ";
                    classes = "text-green-400 font-medium";
                  } else if (oi === res.chosen_index) {
                    indicator = "✗ ";
                    classes = "text-red-400 font-medium";
                  }

                  return (
                    <div key={oi} className={`text-sm pl-2 ${classes}`}>
                      {indicator} {opt}
                    </div>
                  );
                })}
              </div>

              {res.explanation && (
                <div className="text-xs bg-ink-900/60 p-3 rounded-lg border border-white/5 text-text-muted">
                  <span className="font-medium text-text-strong">Key takeaway:</span> {res.explanation}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottom Move to Roadmap CTA */}
      <div className="mt-8 pt-6 border-t border-white/10 flex justify-center">
        <button
          onClick={onContinue}
          className="accent-ring rounded-xl px-8 py-3.5 font-medium text-ink-900 shadow-lg shadow-gold/20 hover:-translate-y-0.5 transition-all flex items-center gap-2"
          style={{ background: "var(--color-gold)" }}
        >
          <span>Move to Roadmap</span>
          <span className="text-lg">→</span>
        </button>
      </div>
    </section>
  );
}
