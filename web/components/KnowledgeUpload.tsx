"use client";

import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { IngestResponse } from "@/lib/types";

interface KnowledgeUploadProps {
  userId: string;
}

// Per-user RAG ingestion (P2 #9): upload a PDF; its text grounds this user's future lessons.
export function KnowledgeUpload({ userId }: KnowledgeUploadProps) {
  const [file, setFile] = useState<File | null>(null);
  const [done, setDone] = useState<IngestResponse | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useMutation({
    mutationFn: (f: File) => api.ingestKnowledge(userId, f),
    onSuccess: (res) => {
      setDone(res);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    },
  });

  return (
    <section className="surface mt-6 p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-xl font-semibold text-text-strong">Your knowledge base</h2>
        <span className="text-xs uppercase tracking-wider text-text-muted">PDF · grounds your lessons</span>
      </div>
      <p className="mt-2 max-w-xl text-sm text-text-muted">
        Upload notes, papers, or docs. MindMorph indexes them and weaves the relevant passages into the
        new paths, lessons and practice it generates for you. Uploads are optional. Your existing path
        and saved lessons stay as they are.
      </p>

      <form
        className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) upload.mutate(file);
        }}
      >
        <label className="accent-ring flex flex-1 cursor-pointer items-center gap-3 rounded-xl border border-dashed border-white/15 bg-ink-850 px-4 py-3 text-text-muted hover:border-white/30">
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => {
              setDone(null);
              setFile(e.target.files?.[0] ?? null);
            }}
          />
          <span className="truncate text-text-strong">
            {file ? file.name : "Choose a PDF…"}
          </span>
        </label>
        <button
          type="submit"
          disabled={!file || upload.isPending}
          className="accent-ring rounded-xl px-5 py-3 font-medium text-ink-900 disabled:opacity-50"
          style={{ background: "var(--color-gold)" }}
        >
          {upload.isPending ? "Indexing…" : "Upload"}
        </button>
      </form>

      {/* Screen-reader-only: "Indexing…" is otherwise visible only in the disabled button label, which
          isn't announced on its own — this covers the wait the same way the dashboard's stage region
          does. Polite + atomic, cleared once done/error take over (those announce themselves below). */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {upload.isPending ? "Indexing…" : ""}
      </div>
      {done && (
        <p role="status" className="mt-3 text-sm" style={{ color: "var(--color-gold)" }}>
          Indexed {done.chunks} passage{done.chunks === 1 ? "" : "s"} from {done.filename}. Available for future generation. Text PDFs only; scanned pages need OCR and are not indexed.
        </p>
      )}
      {upload.isError && (
        <p role="alert" className="mt-3 text-sm" style={{ color: "var(--color-review)" }}>
          {upload.error instanceof Error ? upload.error.message : "Upload failed."}
        </p>
      )}
    </section>
  );
}
