"use client";

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

export interface FindFrameProps {
  /** 1-based. A step past `total` is the result: the bar reads full and there is no count. */
  step: number;
  total: number;
  /** The question, or "Your protocol" on the result: 34/40 with 24 px above. */
  title: string;
  onBack: () => void;
  /** Fixtures mode only: pins the appearance for screenshots. */
  theme?: "light" | "dark";
  /** Fixtures mode only: text size multiplier. */
  scale?: number;
  children: ReactNode;
}

/**
 * The full-screen frame of Find my protocol: no top bar, no tab bar. A sticky
 * row with "Back" (44 target) at the left and "N of 6" at the right, a 2 px
 * progress bar in ink under it, then the title and the step. On every step
 * change the page returns to the top and the title takes focus, so a screen
 * reader hears the new question.
 */
export function FindFrame({ step, total, title, onBack, theme, scale, children }: FindFrameProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  const shown = useRef(step);
  const counted = step <= total;
  const fraction = Math.min(1, Math.max(0, step / total));

  useEffect(() => {
    if (shown.current === step) return;
    shown.current = step;
    window.scrollTo({ top: 0 });
    heading.current?.focus({ preventScroll: true });
  }, [step]);

  return (
    <div
      data-theme={theme}
      style={scale ? ({ "--type-scale": scale } as CSSProperties) : undefined}
      className="mx-auto flex min-h-dvh w-full max-w-[430px] flex-col bg-page text-text"
    >
      <header className="sticky top-0 z-10 bg-page px-gutter" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        <div className="flex h-13 items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="type-secondary pressable -ml-3 min-h-11 rounded-full px-3 font-semibold text-ink active:bg-surface-2"
          >
            Back
          </button>
          {counted ? (
            <p className="type-caption m-0 text-muted tabular-nums">
              {step} of {total}
            </p>
          ) : null}
        </div>
        <div
          role="progressbar"
          aria-label="Find my protocol"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={Math.min(step, total)}
          className="h-0.5 w-full overflow-hidden bg-band"
        >
          <div className="h-full bg-ink transition-[width] duration-200 ease-out" style={{ width: `${fraction * 100}%` }} />
        </div>
      </header>

      <main className="flex flex-1 flex-col px-gutter pb-4">
        <h1 ref={heading} tabIndex={-1} className="type-screen-title m-0 mt-6 text-ink">
          {title}
        </h1>
        {children}
      </main>
    </div>
  );
}
