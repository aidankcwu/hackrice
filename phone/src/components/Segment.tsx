import type { ReactNode } from "react";

/** One option of a two-way toggle: Day / Week, This week / 2 weeks. */
export function Segment({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`type-secondary min-h-9 rounded-full px-4 font-semibold ${on ? "bg-ink text-page" : "bg-surface-2 text-text"}`}
    >
      {children}
    </button>
  );
}
