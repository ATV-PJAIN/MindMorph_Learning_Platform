"use client";

// Rich skeleton loader for lesson generation — occupies the exact same space the real LessonPanel
// will, with shimmer animation, preventing layout shift during the 15-30s LLM compose.

export function LessonSkeleton() {
  return (
    <article className="surface p-7 md:p-10 animate-fade-in">
      {/* Status label */}
      <div className="flex items-center gap-3 mb-6">
        <div className="lesson-skeleton-spinner" />
        <p className="text-sm font-medium text-text-muted">
          Generating your lesson…
        </p>
      </div>

      {/* Eyebrow / skill label placeholder */}
      <div className="skeleton-line h-3 w-24 mb-4" />

      {/* Heading placeholder */}
      <div className="skeleton-line h-6 w-3/4 mb-6" />

      {/* Paragraph block placeholder — 5 lines of varying width */}
      <div className="space-y-3 mb-8">
        <div className="skeleton-line h-3 w-full" />
        <div className="skeleton-line h-3 w-11/12" />
        <div className="skeleton-line h-3 w-4/5" />
        <div className="skeleton-line h-3 w-full" />
        <div className="skeleton-line h-3 w-3/5" />
      </div>

      {/* Second paragraph placeholder */}
      <div className="space-y-3 mb-8">
        <div className="skeleton-line h-3 w-full" />
        <div className="skeleton-line h-3 w-10/12" />
        <div className="skeleton-line h-3 w-5/6" />
      </div>

      {/* Practice section placeholder */}
      <div className="border-t border-white/5 pt-6">
        <div className="skeleton-line h-4 w-40 mb-4" />
        {/* Code editor placeholder */}
        <div
          className="skeleton-block rounded-xl mb-4"
          style={{ height: "160px" }}
        />
        {/* Submit button placeholder */}
        <div className="skeleton-line h-10 w-32 rounded-lg" />
      </div>
    </article>
  );
}
