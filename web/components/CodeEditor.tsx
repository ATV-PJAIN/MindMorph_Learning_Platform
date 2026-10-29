"use client";

import Editor from "@monaco-editor/react";

// Monaco language identifiers for the three supported coding languages.
const MONACO_LANGUAGE: Record<string, string> = {
  python: "python",
  javascript: "javascript",
  sql: "sql",
};

// Monaco editor for coding_challenge solutions (replaces Streamlit's st_ace). Returns the raw code
// string; server-side hardened-sandbox grading is unchanged.
export function CodeEditor({
  value,
  onChange,
  language = "python",
}: {
  value: string;
  onChange: (v: string) => void;
  language?: "python" | "javascript" | "sql" | string;
}) {
  const monacoLang = MONACO_LANGUAGE[language] ?? "python";
  return (
    <div className="overflow-hidden rounded-xl border border-white/10">
      <Editor
        height="300px"
        language={monacoLang}
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{
          fontSize: 14,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          tabSize: language === "python" ? 4 : 2,
          padding: { top: 12, bottom: 12 },
          // Monaco's default accessible name is a generic "Editor content" — name it for what it
          // actually is, since it's the only unlabelled control on the practice exercise.
          ariaLabel: "Your solution",
        }}
      />
    </div>
  );
}
