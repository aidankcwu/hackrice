"use client";

import { Circle, Diamond, Hash, Hexagon, Plus, Square, Star, Triangle, Zap, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { STROKE } from "@/components/ui";
import { DSST_MS, clockLeft, dsstKey, dsstNext, dsstResult, type RunResult } from "@/lib/tests";
import { TestFrame } from "./TestFrame";

/** The nine symbols, by index. Abstract shapes only, none of them an app meaning. */
const SYMBOLS: readonly LucideIcon[] = [Circle, Square, Triangle, Diamond, Hexagon, Star, Plus, Hash, Zap];
const DIGITS = [1, 2, 3, 4, 5, 6, 7, 8, 9] as const;

interface Run {
  start: number;
  /** `key[digit − 1]` is the symbol that digit stands for. */
  key: number[];
  symbol: number;
  correct: number;
  wrong: number;
  done: boolean;
}

interface Shown {
  key: number[];
  symbol: number;
  left: string;
}

export interface DsstScreenProps {
  onFinish: (result: RunResult) => void;
  onEnd: () => void;
  theme?: "light" | "dark";
  scale?: number;
}

/**
 * DSST: 90 s. A key pairs nine symbols with the digits 1–9, shuffled per run
 * and shown as a 3×3 grid of 64 px cells (symbol over digit) above a 96 px
 * symbol; the wearer taps the cell with its digit, and the next symbol
 * follows at once. Score is the correct count.
 */
export function DsstScreen({ onFinish, onEnd, theme, scale }: DsstScreenProps) {
  const [shown, setShown] = useState<Shown | null>(null);
  const run = useRef<Run | null>(null);
  const finish = useRef(onFinish);

  useEffect(() => {
    finish.current = onFinish;
  }, [onFinish]);

  useEffect(() => {
    const start = performance.now();
    const state: Run = { start, key: dsstKey(), symbol: dsstNext(null), correct: 0, wrong: 0, done: false };
    run.current = state;
    let frame = 0;
    let left = "";

    const tick = () => {
      const elapsed = performance.now() - start;
      if (elapsed >= DSST_MS) {
        state.done = true;
        finish.current(dsstResult(state.correct, state.wrong));
        return;
      }
      const next = clockLeft(DSST_MS - elapsed);
      if (next !== left) {
        left = next;
        setShown({ key: state.key, symbol: state.symbol, left: next });
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      state.done = true;
      cancelAnimationFrame(frame);
    };
  }, []);

  const tap = (digit: number) => {
    const state = run.current;
    if (!state || state.done) return;
    if (state.key[digit - 1] === state.symbol) state.correct += 1;
    else state.wrong += 1;
    state.symbol = dsstNext(state.symbol);
    const symbol = state.symbol;
    setShown((current) => (current ? { ...current, symbol } : current));
  };

  const Big = shown ? SYMBOLS[shown.symbol] : null;

  return (
    <TestFrame line={`DSST · ${shown?.left ?? clockLeft(DSST_MS)} left`} onEnd={onEnd} theme={theme} scale={scale}>
      <div role="group" aria-label="Key" className="mx-auto grid w-52 grid-cols-3 gap-2 pt-4">
        {DIGITS.map((digit) => {
          const Icon = shown ? SYMBOLS[shown.key[digit - 1]] : null;
          return (
            <button
              key={digit}
              type="button"
              aria-label={String(digit)}
              disabled={!shown}
              onPointerDown={() => tap(digit)}
              className="pressable flex size-16 flex-col items-center justify-center gap-0.5 rounded-tile bg-surface-2 text-ink disabled:text-muted"
            >
              {Icon ? <Icon size={24} strokeWidth={STROKE} aria-hidden="true" /> : <span className="size-6" />}
              <span className="type-secondary font-semibold tabular-nums" aria-hidden="true">
                {digit}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex flex-1 items-center justify-center">
        {Big ? <Big size={96} strokeWidth={1.5} className="text-ink" role="img" aria-label="Symbol to match" /> : <span className="size-24" />}
      </div>
    </TestFrame>
  );
}
