"use client";

import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import {
  STROOP_COLOURS,
  STROOP_GAP_MS,
  STROOP_MS,
  clockLeft,
  stroopResult,
  stroopTrial,
  type RunResult,
  type StroopColour,
  type StroopTap,
  type StroopTrial,
} from "@/lib/tests";
import { TestFrame } from "./TestFrame";

/**
 * The stimulus inks. Red and green are the app's data colours; blue and yellow
 * have no token that reads as their name (the amber token is a brown in light),
 * so they are set here as variables on the screen (light and dark). Stimulus
 * colours are the one allowed exception to the token rule.
 */
const INK: Record<StroopColour, string> = {
  red: "var(--bad)",
  green: "var(--good)",
  blue: "var(--stroop-blue)",
  yellow: "var(--stroop-yellow)",
};
const SCREEN_STYLE = {
  "--stroop-blue": "light-dark(#3b82f6, #60a5fa)",
  "--stroop-yellow": "light-dark(#d4a300, #facc15)",
} as CSSProperties;

const LABEL: Record<StroopColour, string> = { red: "Red", green: "Green", blue: "Blue", yellow: "Yellow" };

interface Run {
  start: number;
  trial: StroopTrial | null;
  onsetAt: number;
  /** While `trial` is null: when the next word appears. */
  gapUntil: number;
  taps: StroopTap[];
  done: boolean;
}

interface Shown {
  trial: StroopTrial | null;
  left: string;
}

export interface StroopScreenProps {
  onFinish: (result: RunResult) => void;
  onEnd: () => void;
  theme?: "light" | "dark";
  scale?: number;
}

/**
 * Stroop: 60 s. A colour word at 56 in an ink that matches it half the time;
 * four 64-tall pills carry the colour names in plain text, and the wearer taps
 * the ink. Score is the interference, mean RT incongruent − congruent.
 */
export function StroopScreen({ onFinish, onEnd, theme, scale }: StroopScreenProps) {
  const [shown, setShown] = useState<Shown>({ trial: null, left: clockLeft(STROOP_MS) });
  const run = useRef<Run | null>(null);
  const finish = useRef(onFinish);

  useEffect(() => {
    finish.current = onFinish;
  }, [onFinish]);

  useEffect(() => {
    const start = performance.now();
    const state: Run = { start, trial: null, onsetAt: start, gapUntil: start + STROOP_GAP_MS, taps: [], done: false };
    run.current = state;
    let frame = 0;
    let left = "";

    const tick = () => {
      const now = performance.now();
      const elapsed = now - start;
      if (elapsed >= STROOP_MS) {
        state.done = true;
        finish.current(stroopResult(state.taps));
        return;
      }
      let changed = false;
      if (state.trial === null && now >= state.gapUntil) {
        state.trial = stroopTrial();
        state.onsetAt = now;
        changed = true;
      }
      const nextLeft = clockLeft(STROOP_MS - elapsed);
      if (nextLeft !== left) {
        left = nextLeft;
        changed = true;
      }
      if (changed) setShown({ trial: state.trial, left: nextLeft });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      state.done = true;
      cancelAnimationFrame(frame);
    };
  }, []);

  const tap = (event: PointerEvent<HTMLButtonElement>) => {
    const state = run.current;
    const colour = event.currentTarget.dataset.colour as StroopColour | undefined;
    if (!state || state.done || state.trial === null || !colour) return;
    const now = performance.now();
    state.taps.push({ congruent: state.trial.congruent, correct: colour === state.trial.ink, rt: Math.round(now - state.onsetAt) });
    state.trial = null;
    state.gapUntil = now + STROOP_GAP_MS;
    setShown((current) => ({ ...current, trial: null }));
  };

  return (
    <TestFrame line={`Stroop · ${shown.left} left`} onEnd={onEnd} theme={theme} scale={scale}>
      <div style={SCREEN_STYLE} className="flex flex-1 flex-col">
        <div className="flex flex-1 items-center justify-center">
          <span aria-live="off" className="type-hero" style={shown.trial ? { color: INK[shown.trial.ink] } : undefined}>
            {shown.trial ? LABEL[shown.trial.word] : " "}
          </span>
        </div>
        <div role="group" aria-label="Ink colour" className="grid grid-cols-2 gap-2 pb-4">
          {STROOP_COLOURS.map((colour) => (
            <button
              key={colour}
              type="button"
              data-colour={colour}
              onPointerDown={tap}
              className="type-body pressable flex min-h-16 items-center justify-center rounded-full bg-surface-2 px-4 font-semibold text-ink"
            >
              {LABEL[colour]}
            </button>
          ))}
        </div>
      </div>
    </TestFrame>
  );
}
