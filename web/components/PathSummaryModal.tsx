import type { LearningSession } from "@/lib/types";
import { Modal } from "./Modal";
import { EvidenceSources, citedText } from "./EvidenceSources";

interface PathSummaryModalProps {
  session: LearningSession;
  query: string;
  domain: string;
  onExplore: () => void;
}

const FORMAT_LABELS: Record<string, string> = {
  A: "5-Minute Boost",
  B: "20-Min Builder",
  C: "2-Hour Sprint",
};

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

export function PathSummaryModal({ session, query, domain, onExplore }: PathSummaryModalProps) {
  const nodeCount = session.skill_graph.nodes.length;
  const edgeCount = session.skill_graph.edges.length;
  const format = FORMAT_LABELS[session.format_type ?? "B"] ?? "Builder";
  const emoji = getDomainEmoji(domain);

  // Derive level breakdown
  const levels = session.skill_graph.nodes.reduce(
    (acc, n) => {
      const level = n.level ?? "foundational";
      acc[level] = (acc[level] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  return (
    <Modal label="Your learning path" onEscape={onExplore}>
        {/* Success banner */}
        <div
          className="px-6 py-5 text-center"
          style={{
            background: "linear-gradient(135deg, oklch(80% 0.12 85 / 0.12), oklch(78% 0.14 165 / 0.08))",
          }}
        >
          <div className="text-4xl mb-2">{emoji}</div>
          <h2 className="text-xl font-semibold text-text-strong">Your Learning Path is Ready!</h2>
          <p className="mt-1 text-sm text-text-muted max-w-sm mx-auto">{query}</p>
        </div>

        <div className="p-6 md:p-8">
          {/* Summary */}
          {session.summary && (
            <p className="text-sm text-text mb-5 leading-relaxed">{citedText(session.summary, session.grounding)}</p>
          )}

          {/* Stats grid */}
          <div className="grid grid-cols-3 gap-3 mb-6">
            <div className="rounded-xl border border-white/8 bg-ink-900/40 p-3.5 text-center">
              <p className="text-2xl font-semibold text-text-strong">{nodeCount}</p>
              <p className="text-xs text-text-muted mt-0.5">Skills</p>
            </div>
            <div className="rounded-xl border border-white/8 bg-ink-900/40 p-3.5 text-center">
              <p className="text-2xl font-semibold text-text-strong">{edgeCount}</p>
              <p className="text-xs text-text-muted mt-0.5">Connections</p>
            </div>
            <div className="rounded-xl border border-white/8 bg-ink-900/40 p-3.5 text-center">
              <p className="text-2xl font-semibold text-text-strong" style={{ fontSize: "0.95rem" }}>
                {format}
              </p>
              <p className="text-xs text-text-muted mt-0.5">Format</p>
            </div>
          </div>

          {/* Level breakdown */}
          <div className="mb-6">
            <p className="eyebrow mb-2.5">Skill Levels</p>
            <div className="flex gap-2">
              {levels.foundational && (
                <span className="rounded-lg px-3 py-1.5 text-xs font-medium border border-white/8 bg-ink-900/40 text-text-muted">
                  🟢 {levels.foundational} Foundational
                </span>
              )}
              {levels.intermediate && (
                <span className="rounded-lg px-3 py-1.5 text-xs font-medium border border-white/8 bg-ink-900/40 text-text-muted">
                  🟡 {levels.intermediate} Intermediate
                </span>
              )}
              {levels.advanced && (
                <span className="rounded-lg px-3 py-1.5 text-xs font-medium border border-white/8 bg-ink-900/40 text-text-muted">
                  🔴 {levels.advanced} Advanced
                </span>
              )}
            </div>
          </div>

          {/* Review note if applicable */}
          {session.review_passed === false && (
            <div
              className="mb-5 rounded-lg border-l-2 py-2.5 pl-3 text-xs text-text-muted"
              style={{ borderColor: "var(--color-gold-dim)", background: "oklch(80% 0.12 85 / 0.04)" }}
            >
              Our reviewer flagged a possible gap — still fully usable.
            </div>
          )}

          <EvidenceSources evidence={session.grounding} />

          {/* CTA */}
          <button
            onClick={onExplore}
            className="accent-ring w-full rounded-xl px-6 py-3.5 font-medium text-ink-900 transition-all hover:-translate-y-0.5 flex items-center justify-center gap-2"
            style={{ background: "var(--color-gold)" }}
          >
            Explore Your Path
            <span className="text-lg">→</span>
          </button>
        </div>
    </Modal>
  );
}
