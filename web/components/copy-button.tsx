"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { BUTTON } from "@/lib/ui";

/** Copies a short text (a command) to the clipboard and says so for two seconds. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={`${BUTTON.secondary} ${BUTTON.small}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        } catch {
          // Clipboard blocked (insecure origin): the text stays selectable.
        }
      }}
      type="button"
    >
      {copied ? <Check aria-hidden="true" size={14} /> : <Copy aria-hidden="true" size={14} />}
      {copied ? "Copied" : label}
    </button>
  );
}
