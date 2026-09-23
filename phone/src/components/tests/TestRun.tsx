"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { Button, Chip, MEANING_ICONS } from "@/components/ui";
import { TEST_STEPS, TESTS } from "@/content/tests";
import type { TestDef } from "@/content/types";
import { durationLabel, saveResult, type RunResult, type TestResult } from "@/lib/tests";
import { DsstScreen } from "./DsstScreen";
import { NBackScreen } from "./NBackScreen";
import { PvtScreen } from "./PvtScreen";
import { Results } from "./Results";
import { StroopScreen } from "./StroopScreen";
import { TestFrame } from "./TestFrame";

type Phase = { kind: "intro" } | { kind: "run" } | { kind: "done"; entry: TestResult };

export interface TestRunProps {
  id: TestDef["id"];
  theme?: "light" | "dark";
  scale?: number;
}

/**
 * One test, full screen: the guided start, then the run, then the result on
 * the same route. The run's result is written to the store the moment the
 * test ends, before the result screen mounts.
 */
export function TestRun({ id, theme, scale }: TestRunProps) {
  const router = useRouter();
  const test = TESTS.find((candidate) => candidate.id === id) ?? TESTS[0];
  const [phase, setPhase] = useState<Phase>({ kind: "intro" });

  const end = useCallback(() => router.push("/tests"), [router]);
  const finish = useCallback(
    (result: RunResult) => {
      setPhase({ kind: "done", entry: saveResult(test.id, result) });
    },
    [test.id],
  );

  if (phase.kind === "done") return <Results test={test} entry={phase.entry} theme={theme} scale={scale} />;

  if (phase.kind === "run") {
    const screen = { onFinish: finish, onEnd: end, theme, scale };
    switch (test.id) {
      case "pvt":
        return <PvtScreen {...screen} />;
      case "nback":
        return <NBackScreen {...screen} />;
      case "dsst":
        return <DsstScreen {...screen} />;
      case "stroop":
        return <StroopScreen {...screen} />;
    }
  }

  return (
    <TestFrame line="Guided start" onEnd={end} theme={theme} scale={scale}>
      <div className="flex flex-1 flex-col pt-4">
        <h1 className="type-screen-title m-0 text-ink">{test.name}</h1>
        <p className="type-body m-0 mt-2 text-text">{test.instruction}</p>
        <div className="mt-3">
          <Chip icon={MEANING_ICONS.test}>{durationLabel(test.seconds)}</Chip>
        </div>
        <ol className="m-0 mt-section flex list-none flex-col gap-2 p-0">
          {TEST_STEPS[test.id].map((step, i) => (
            <li key={step} className="type-secondary flex gap-3 text-muted">
              <span className="w-4 shrink-0 text-right tabular-nums">{i + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        <div className="mt-auto pt-6 pb-4">
          <Button onClick={() => setPhase({ kind: "run" })}>Start</Button>
        </div>
      </div>
    </TestFrame>
  );
}
