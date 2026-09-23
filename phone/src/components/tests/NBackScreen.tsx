"use client";

import { useEffect, useRef, useState } from "react";
import {
  NBACK_COUNT,
  NBACK_ISI_MS,
  NBACK_MS,
  NBACK_N,
  NBACK_ON_MS,
  clockLeft,
  nbackResult,
  nbackSequence,
  type NbackAnswer,
  type NbackSequence,
  type RunResult,
} from "@/lib/tests";
import { TestFrame } from "./TestFrame";

interface Run {
  start: number;
  sequence: NbackSequence;
  answers: NbackAnswer[];
  done: boolean;
}

interface Shown {
  /** The current letter's index, −1 before the first. */
  index: number;
  /** The letter on screen; null in the half second after it goes. */
  letter: string | null;
  /** What was tapped for this letter. */
  answered: NbackAnswer;
  left: string;
}

const BEFORE: Shown = { index: -1, letter: null, answered: null, left: clockLeft(NBACK_MS) };

export interface NBackScreenProps {
  onFinish: (result: RunResult) => void;
  onEnd: () => void;
  theme?: "light" | "dark";
  scale?: number;
}

/** 96, centred. */
const LETTER = "text-[length:calc(6rem*var(--type-scale))] leading-none font-semibold text-ink";
const PILL = "pressable motion flex min-h-16 items-center justify-center rounded-full px-4 type-body font-semibold";

/**
 * 2-back: two minutes, a letter every 2.5 s shown for 2 s. "Match" when it is
 * the same as the one two back, "No match" otherwise; the first two letters
 * take no answer. One tap per letter counts.
 */
export function NBackScreen({ onFinish, onEnd, theme, scale }: NBackScreenProps) {
  const [shown, setShown] = useState<Shown>(BEFORE);
  const run = useRef<Run | null>(null);
  const finish = useRef(onFinish);

  useEffect(() => {
    finish.current = onFinish;
  }, [onFinish]);

  useEffect(() => {
    const start = performance.now();
    const state: Run = {
      start,
      sequence: nbackSequence(),
      answers: Array.from({ length: NBACK_COUNT }, () => null),
      done: false,
    };
    run.current = state;
    let frame = 0;
    let last: Shown = BEFORE;

    const tick = () => {
      const now = performance.now();
      const elapsed = now - start;
      const index = Math.floor(elapsed / NBACK_ISI_MS);
      if (index >= NBACK_COUNT) {
        state.done = true;
        finish.current(nbackResult(state.sequence.targets, state.answers));
        return;
      }
      const visible = elapsed - index * NBACK_ISI_MS < NBACK_ON_MS;
      const next: Shown = {
        index,
        letter: visible ? state.sequence.letters[index] : null,
        answered: state.answers[index],
        left: clockLeft(NBACK_MS - elapsed),
      };
      if (next.index !== last.index || next.letter !== last.letter || next.answered !== last.answered || next.left !== last.left) {
        last = next;
        setShown(next);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      state.done = true;
      cancelAnimationFrame(frame);
    };
  }, []);

  const answer = (kind: NbackAnswer) => {
    const state = run.current;
    if (!state || state.done) return;
    const index = Math.floor((performance.now() - state.start) / NBACK_ISI_MS);
    if (index < NBACK_N || index >= NBACK_COUNT || state.answers[index] !== null) return;
    state.answers[index] = kind;
    setShown((current) => (current.index === index ? { ...current, answered: kind } : current));
  };

  const canAnswer = shown.index >= NBACK_N;

  return (
    <TestFrame line={`2-back · ${shown.left} left`} onEnd={onEnd} theme={theme} scale={scale}>
      <div className="flex flex-1 items-center justify-center">
        <span aria-live="off" className={LETTER}>
          {shown.letter ?? " "}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 pb-4">
        <button
          type="button"
          aria-pressed={shown.answered === "match"}
          disabled={!canAnswer}
          onPointerDown={() => answer("match")}
          className={`${PILL} ${shown.answered === "match" ? "bg-ink text-page" : "bg-surface-2 text-ink"} disabled:text-muted`}
        >
          Match
        </button>
        <button
          type="button"
          aria-pressed={shown.answered === "nomatch"}
          disabled={!canAnswer}
          onPointerDown={() => answer("nomatch")}
          className={`${PILL} ${shown.answered === "nomatch" ? "bg-ink text-page" : "bg-surface-2 text-ink"} disabled:text-muted`}
        >
          No match
        </button>
      </div>
    </TestFrame>
  );
}
