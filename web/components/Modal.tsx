"use client";

import { useEffect, useRef, type ReactNode } from "react";

export function Modal({ label, onEscape, children, returnFocusId }: { label: string; onEscape: () => void; children: ReactNode; returnFocusId?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const escape = useRef(onEscape);
  escape.current = onEscape;
  useEffect(() => {
    const previous = (returnFocusId ? document.getElementById(returnFocusId) : null) ?? document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const root = container.current!;
    const focusable = () => Array.from(root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), a[href], textarea:not([disabled]), [tabindex="0"]',
    )).filter((element) => element.getClientRects().length > 0);
    (focusable()[0] ?? root).focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); escape.current(); }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0] ?? root;
      const last = elements.at(-1) ?? root;
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    const containFocus = (event: FocusEvent) => {
      if (!root.contains(event.target as Node)) (focusable()[0] ?? root).focus();
    };
    document.addEventListener("keydown", keydown);
    document.addEventListener("focusin", containFocus);
    return () => {
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("focusin", containFocus);
      document.body.style.overflow = previousOverflow;
      if (previous?.isConnected) previous.focus();
    };
  }, [returnFocusId]);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
    <div ref={container} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}
      className="surface w-full max-w-lg max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl border border-white/10 shadow-2xl">
      {children}
    </div>
  </div>;
}
