"use client";

import type { CSSProperties, ReactNode } from "react";

export interface TestFrameProps {
  /** The screen goes black in both appearances (PVT-B). */
  dark?: boolean;
  /** One 13 muted line across the top: the instruction, or the name and the time left. */
  line?: string;
  /** "End", top-left: abandons the run without saving. Omitted on the result screen. */
  onEnd?: () => void;
  /** Fixtures mode only: pins the appearance for screenshots. */
  theme?: "light" | "dark";
  /** Fixtures mode only: text size multiplier. */
  scale?: number;
  children: ReactNode;
}

/**
 * The full-screen frame a test runs in: page fill, no top bar, no tab bar.
 * A 44 px "End" text button top-left and one muted line; the test fills the
 * rest. `dark` pins the appearance to dark, which is how PVT-B stays black
 * under both themes (`globals.css` reads `[data-theme]` from any element).
 */
export function TestFrame({ dark = false, line, onEnd, theme, scale, children }: TestFrameProps) {
  return (
    <div
      data-theme={dark ? "dark" : theme}
      style={{
        ...(scale ? ({ "--type-scale": scale } as CSSProperties) : null),
        paddingTop: "env(safe-area-inset-top)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
      className="mx-auto flex min-h-dvh w-full max-w-[430px] flex-col bg-page text-text"
    >
      <div className="relative flex h-13 shrink-0 items-center px-2">
        {onEnd ? (
          <button
            type="button"
            onClick={onEnd}
            className="type-secondary pressable min-h-11 rounded-full px-3 font-semibold text-ink active:bg-surface-2"
          >
            End
          </button>
        ) : null}
        {line ? (
          <p className="type-caption pointer-events-none absolute inset-x-16 m-0 truncate text-center text-muted" aria-live="off">
            {line}
          </p>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col px-gutter">{children}</div>
    </div>
  );
}
