"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  PVT_EARLY_MS,
  PVT_FEEDBACK_MS,
  PVT_MS,
  PVT_TIMEOUT_MS,
  pvtResult,
  pvtWait,
  type RunResult,
} from "@/lib/tests";
import { TestFrame } from "./TestFrame";

type Phase = "wait" | "count" | "feedback" | "early";

interface Run {
  start: number;
  phase: Phase;
  /** When the current phase began (for "count", the counter's onset). */
  phaseAt: number;
  /** In "wait": when the counter starts. */
  onsetAt: number;
  rts: number[];
  falseStarts: number;
  timeouts: number;
  done: boolean;
}

interface Shown {
  phase: Phase;
  /** The frozen reaction time in "feedback". */
  value: number;
}

export interface PvtScreenProps {
  onFinish: (result: RunResult) => void;
  onEnd: () => void;
  theme?: "light" | "dark";
  scale?: number;
}

export const PVT_LINE = "Tap when the number appears";

/** 64 tabular, centred. */
const COUNTER =
  "text-[length:calc(4rem*var(--type-scale))] leading-[calc(4.5rem*var(--type-scale))] font-semibold text-ink tabular-nums";

/**
 * PVT-B: a black screen for three minutes. Each trial waits 2–10 s, then a
 * counter of milliseconds runs until the tap; the whole screen is the target.
 * A tap before the counter is a false start. Timing runs on
 * `requestAnimationFrame` and `performance.now`; the running counter is written
 * straight into its span so React never renders 60 times a second.
 */
export function PvtScreen({ onFinish, onEnd, theme, scale }: PvtScreenProps) {
  const [shown, setShown] = useState<Shown>({ phase: "wait", value: 0 });
  const run = useRef<Run | null>(null);
  const counter = useRef<HTMLSpanElement>(null);
  const finish = useRef(onFinish);

  useEffect(() => {
    finish.current = onFinish;
  }, [onFinish]);

  useEffect(() => {
    const start = performance.now();
    const state: Run = {
      start,
      phase: "wait",
      phaseAt: start,
      onsetAt: start + pvtWait(),
      rts: [],
      falseStarts: 0,
      timeouts: 0,
      done: false,
    };
    run.current = state;
    let frame = 0;

    const toWait = (now: number) => {
      state.phase = "wait";
      state.phaseAt = now;
      state.onsetAt = now + pvtWait();
      setShown({ phase: "wait", value: 0 });
    };

    const tick = () => {
      const now = performance.now();
      if (state.phase === "wait") {
        // Ends at three minutes, or as soon as the pending onset lies past them: no trial starts that cannot complete.
        if (now - state.start >= PVT_MS || state.onsetAt - state.start >= PVT_MS) {
          state.done = true;
          finish.current(pvtResult(state.rts, state.falseStarts, state.timeouts));
          return;
        }
        if (now >= state.onsetAt) {
          state.phase = "count";
          state.phaseAt = now;
          setShown({ phase: "count", value: 0 });
        }
      } else if (state.phase === "count") {
        const elapsed = now - state.phaseAt;
        if (counter.current) counter.current.textContent = String(Math.floor(elapsed));
        if (elapsed >= PVT_TIMEOUT_MS) {
          state.timeouts += 1;
          toWait(now);
        }
      } else if (state.phase === "feedback") {
        if (now - state.phaseAt >= PVT_FEEDBACK_MS) toWait(now);
      } else if (now - state.phaseAt >= PVT_EARLY_MS) {
        toWait(now);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      state.done = true;
      cancelAnimationFrame(frame);
    };
  }, []);

  const tap = () => {
    const state = run.current;
    if (!state || state.done) return;
    const now = performance.now();
    if (state.phase === "wait") {
      state.falseStarts += 1;
      state.phase = "early";
      state.phaseAt = now;
      setShown({ phase: "early", value: 0 });
    } else if (state.phase === "count") {
      const rt = Math.round(now - state.phaseAt);
      state.rts.push(rt);
      state.phase = "feedback";
      state.phaseAt = now;
      setShown({ phase: "feedback", value: rt });
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      tap();
    }
  };

  return (
    <TestFrame dark line={PVT_LINE} onEnd={onEnd} theme={theme} scale={scale}>
      <button
        type="button"
        aria-label={PVT_LINE}
        onPointerDown={tap}
        onKeyDown={onKeyDown}
        className="-mx-gutter flex flex-1 touch-none items-center justify-center select-none"
      >
        {shown.phase === "count" ? (
          <span key="count" ref={counter} className={COUNTER}>
            0
          </span>
        ) : shown.phase === "feedback" ? (
          <span key="feedback" className={COUNTER}>
            {shown.value}
          </span>
        ) : shown.phase === "early" ? (
          <span className="type-body text-muted">Too early</span>
        ) : null}
      </button>
    </TestFrame>
  );
}
