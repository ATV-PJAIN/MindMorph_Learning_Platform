import { FollowUpQuestion } from "@/lib/types";
import { useState, useCallback } from "react";
import { Modal } from "./Modal";

interface FollowUpModalProps {
  questions: FollowUpQuestion[];
  domain: string;
  onSubmit: (answers: Record<string, string>) => void;
  onCancel: () => void;
  submitting?: boolean;
}

const DOMAIN_EMOJI: Record<string, string> = {
  programming: "💻",
  "software engineering": "💻",
  "data science": "📊",
  "machine learning": "🤖",
  ai: "🤖",
  music: "🎵",
  business: "📈",
  finance: "💰",
  design: "🎨",
  mathematics: "📐",
  science: "🔬",
  cooking: "🍳",
  writing: "✍️",
  languages: "🌍",
  health: "💪",
  fitness: "💪",
  sports: "⚽",
};

function getDomainEmoji(domain: string): string {
  const lower = domain.toLowerCase();
  for (const [key, emoji] of Object.entries(DOMAIN_EMOJI)) {
    if (lower.includes(key)) return emoji;
  }
  return "🎯";
}

export function FollowUpModal({ questions, domain, onSubmit, onCancel, submitting }: FollowUpModalProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    questions.forEach((q) => {
      initial[q.id] = q.options[q.default || 0] || "";
    });
    return initial;
  });
  const [otherActive, setOtherActive] = useState<Record<string, boolean>>({});
  const [otherText, setOtherText] = useState<Record<string, string>>({});
  const [direction, setDirection] = useState<"forward" | "backward">("forward");

  const current = questions[currentStep];
  const isLast = currentStep === questions.length - 1;
  const isFirst = currentStep === 0;
  const emoji = getDomainEmoji(domain);

  const isOther = current ? otherActive[current.id] ?? false : false;

  const handleSelectPreset = (opt: string) => {
    setOtherActive({ ...otherActive, [current.id]: false });
    setAnswers({ ...answers, [current.id]: opt });
  };

  const handleSelectOther = () => {
    setOtherActive({ ...otherActive, [current.id]: true });
    setAnswers({ ...answers, [current.id]: otherText[current.id] || "Other" });
  };

  const handleOtherTextChange = (text: string) => {
    setOtherText({ ...otherText, [current.id]: text });
    setAnswers({ ...answers, [current.id]: text || "Other" });
  };

  const goNext = useCallback(() => {
    if (isLast) {
      onSubmit(answers);
    } else {
      setDirection("forward");
      setCurrentStep((s) => s + 1);
    }
  }, [isLast, answers, onSubmit]);

  const goBack = useCallback(() => {
    if (!isFirst) {
      setDirection("backward");
      setCurrentStep((s) => s - 1);
    }
  }, [isFirst]);

  if (!current) return <Modal label="Learning preferences" onEscape={onCancel} returnFocusId="generate-path">
    <div className="p-6"><p>No questions are available.</p><button onClick={() => onSubmit({})}>Continue without questions</button></div>
  </Modal>;

  return (
    <Modal label="Learning preferences" onEscape={onCancel} returnFocusId="generate-path">
        {/* Progress bar */}
        <div className="h-1 w-full bg-ink-900/60">
          <div
            className="h-full transition-all duration-500"
            style={{
              width: `${((currentStep + 1) / questions.length) * 100}%`,
              background: "var(--color-gold)",
            }}
          />
        </div>

        <div className="p-6 md:p-8">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-2xl">{emoji}</span>
              <div>
                <p className="eyebrow mb-0.5">{domain}</p>
                <p className="text-xs text-text-muted">
                  Question {currentStep + 1} of {questions.length}
                </p>
              </div>
            </div>
            {/* Step dots */}
            <div className="flex gap-1.5">
              {questions.map((_, idx) => (
                <div
                  key={idx}
                  className="h-2 w-2 rounded-full transition-all duration-300"
                  style={{
                    background:
                      idx === currentStep
                        ? "var(--color-gold)"
                        : idx < currentStep
                        ? "var(--color-mastered)"
                        : "oklch(100% 0 0 / 0.12)",
                    transform: idx === currentStep ? "scale(1.3)" : "scale(1)",
                  }}
                />
              ))}
            </div>
          </div>

          {/* Question card — animated */}
          <div
            key={`${currentStep}-${direction}`}
            className="followup-card-enter"
            style={{
              animationName:
                direction === "forward" ? "followup-slide-in-right" : "followup-slide-in-left",
            }}
          >
            <h3 className="text-lg font-semibold text-text-strong mb-5">{current.question}</h3>

            <div className="space-y-2.5">
              {current.options && current.options.length > 0 ? (
                <>
                  {current.options.map((opt) => {
                    const isSelected = !isOther && answers[current.id] === opt;
                    return (
                      <label
                        key={opt}
                        className={`flex cursor-pointer items-center rounded-xl border p-4 transition-all duration-200 ${
                          isSelected
                            ? "border-gold/50 bg-gold/10 -translate-y-0.5 shadow-lg shadow-gold/5"
                            : "border-white/10 bg-ink-900/50 hover:bg-white/5 hover:border-white/20"
                        }`}
                      >
                        <input
                          type="radio"
                          name={current.id}
                          value={opt}
                          checked={isSelected}
                          onChange={() => handleSelectPreset(opt)}
                          className="h-4 w-4 text-gold focus:ring-gold bg-ink-900 border-white/20"
                        />
                        <span className="ml-3 block text-sm font-medium text-text-strong">{opt}</span>
                      </label>
                    );
                  })}

                  {/* "Other" Option */}
                  <div
                    className={`rounded-xl border transition-all duration-200 overflow-hidden ${
                      isOther
                        ? "border-gold/50 bg-gold/10 shadow-lg shadow-gold/5"
                        : "border-white/10 bg-ink-900/50 hover:bg-white/5 hover:border-white/20"
                    }`}
                  >
                    <label className="flex cursor-pointer items-center p-4">
                      <input
                        type="radio"
                        name={current.id}
                        value="__other__"
                        checked={isOther}
                        onChange={handleSelectOther}
                        className="h-4 w-4 text-gold focus:ring-gold bg-ink-900 border-white/20"
                      />
                      <span className="ml-3 block text-sm font-medium text-text-strong">
                        Other (type your own answer)
                      </span>
                    </label>

                    {isOther && (
                      <div className="px-4 pb-4 pt-1 animate-fade-in">
                        <input
                          type="text"
                          value={otherText[current.id] || ""}
                          onChange={(e) => handleOtherTextChange(e.target.value)}
                          placeholder="Type your own answer (e.g. none, no, or custom preference)..."
                          autoFocus
                          className="w-full rounded-lg border border-white/15 bg-ink-900/80 px-3.5 py-2.5 text-sm text-text-strong placeholder:text-text-muted/60 focus:border-gold/50 focus:outline-none focus:ring-1 focus:ring-gold/50"
                        />
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <input
                  type="text"
                  name={current.id}
                  value={answers[current.id] || ""}
                  onChange={(e) => setAnswers({ ...answers, [current.id]: e.target.value })}
                  placeholder="Type your answer here..."
                  className="w-full rounded-xl border border-white/10 bg-ink-900/50 p-4 text-sm text-text-strong focus:border-gold/50 focus:ring-1 focus:ring-gold/50"
                />
              )}
            </div>
          </div>

          {/* Navigation */}
          <div className="mt-8 flex items-center justify-between pt-4 border-t border-white/10">
            <div className="flex gap-2">
              {!isFirst && (
                <button
                  type="button"
                  onClick={goBack}
                  disabled={submitting}
                  className="rounded-xl px-4 py-2.5 text-sm font-medium text-text-muted hover:text-text transition-colors disabled:opacity-50 flex items-center gap-1.5"
                >
                  ← Back
                </button>
              )}
              <button
                type="button"
                  onClick={() => onSubmit({})}
                disabled={submitting}
                className="rounded-xl px-4 py-2.5 text-sm font-medium text-text-muted hover:text-text transition-colors disabled:opacity-50"
              >
                Skip All
              </button>
            </div>
            <button
              type="button"
              onClick={goNext}
              disabled={submitting}
              className="accent-ring rounded-xl px-6 py-2.5 font-medium text-ink-900 transition-all hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0 flex items-center gap-2"
              style={{ background: "var(--color-gold)" }}
            >
              {submitting ? (
                "Generating..."
              ) : isLast ? (
                <>
                  Generate Path
                  <span className="text-lg">→</span>
                </>
              ) : (
                <>
                  Next
                  <span className="text-lg">→</span>
                </>
              )}
            </button>
          </div>
        </div>
    </Modal>
  );
}
