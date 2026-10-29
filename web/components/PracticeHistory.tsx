import { useState } from "react";

export interface PracticeAttempt {
  score: number;
  ts?: string;
}

export function practiceAttempts(events: unknown, nodeId: string): PracticeAttempt[] {
  if (!Array.isArray(events)) return [];
  return events
    .filter((event): event is Record<string, unknown> => {
      if (!event || typeof event !== "object") return false;
      const score = (event as Record<string, unknown>).score;
      return (event as Record<string, unknown>).stage === "exercise_graded" &&
        (event as Record<string, unknown>).node_id === nodeId &&
        typeof score === "number" && Number.isFinite(score) && score >= 0 && score <= 100;
    })
    .reverse()
    .map((event) => ({ score: event.score as number, ts: typeof event.ts === "string" ? event.ts : undefined }));
}

function displayDate(ts?: string): string {
  if (!ts || !Number.isFinite(Date.parse(ts))) return "Date unavailable";
  return new Date(ts).toLocaleString();
}

export function PracticeHistory({ events, nodeId, label }: { events: unknown; nodeId: string; label: string }) {
  const [expanded, setExpanded] = useState(false);
  const attempts = practiceAttempts(events, nodeId);
  const shown = expanded ? attempts : attempts.slice(0, 5);

  return (
    <section aria-labelledby="practice-history-heading" className="surface mt-6 overflow-hidden p-5 md:p-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="practice-history-heading" className="text-lg font-semibold text-text-strong">Practice history: {label}</h2>
        {attempts.length > 5 && (
          <button type="button" className="accent-ring rounded px-3 py-2 text-sm text-text-muted hover:text-text" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
            {expanded ? "Show less" : "Show all"}
          </button>
        )}
      </div>
      {attempts.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No recorded practice attempts yet</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[280px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-text-muted"><tr><th scope="col" className="py-2 pr-4">Score</th><th scope="col" className="py-2">Date</th></tr></thead>
            <tbody>{shown.map((attempt, index) => <tr key={`${attempt.ts ?? "missing"}-${index}`} className="border-t border-white/5"><td className="py-2 pr-4 font-medium text-text">{attempt.score}%</td><td className="py-2 text-text-muted">{displayDate(attempt.ts)}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
