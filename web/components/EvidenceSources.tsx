import type { GroundingInfo } from "@/lib/types";

export function citedText(text: string, evidence?: GroundingInfo | null): string {
  return text.replace(/\[source:([^\]\s]+)\]/g, (_match, id: string) => {
    const index = evidence?.passages.findIndex((p) => p.id === id) ?? -1;
    return index >= 0 ? `[${index + 1}]` : "[source unavailable]";
  });
}

export function EvidenceSources({ evidence }: { evidence?: GroundingInfo | null }) {
  if (!evidence) return <p className="mt-3 text-xs text-text-muted">Sources were not recorded for this saved content.</p>;
  const message = {
    grounded: "Evidence available",
    insufficient: "Limited evidence. Some details may rely on model knowledge.",
    disabled: "Additional source retrieval is disabled for this request.",
    unavailable: "Supporting sources could not be retrieved. Treat this content as limited evidence.",
  }[evidence.status];
  return (
    <section aria-label="Sources" className="my-4 rounded-xl border border-white/10 p-3 text-sm">
      <p role="status" className="text-text-muted">{message}</p>
      {evidence.warnings.includes("unsupported_citation") && (
        <p className="mt-1 text-text-muted">An unsupported source reference was removed.</p>
      )}
      {evidence.passages.length > 0 && (
        <details className="mt-2">
          <summary className="accent-ring cursor-pointer rounded text-text-strong">Sources used for generation ({evidence.passages.length})</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5">
            {evidence.passages.map((source) => (
              <li key={source.id} className="break-words">
                {source.url && /^https?:\/\//i.test(source.url) ? (
                  <a href={source.url} target="_blank" rel="noopener noreferrer" className="accent-ring rounded underline">{source.source}</a>
                ) : source.source}
                {source.page != null && ` · page ${source.page}`}
                {source.kind === "web_snippet" && " · search snippet"}
                {source.kind === "web_page" && " · public page"}
                {source.kind === "shared" && " · shared material"}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
