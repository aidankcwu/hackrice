"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ProtocolReview } from "@/components/library/ProtocolReview";
import { Button, ErrorState } from "@/components/ui";
import { DEVICES } from "@/content/devices";
import { FIND_STEPS, pickTemplate, shiftWindows, templateWake, type FindAnswers } from "@/content/find";
import { protocolById } from "@/content/protocols";
import { TREATMENTS } from "@/content/treatments";
import type { FindStep } from "@/content/types";
import { readConcierge, writeConcierge } from "@/lib/conciergeStore";
import { FindFrame } from "./FindFrame";
import { StepScreen, type StepOption } from "./StepScreen";

const TOTAL = FIND_STEPS.length;
/** The wake the flow assumes without an answer: the one the templates are written around. */
const DEFAULT_WAKE = "06:30";

/** Answers for a fixtures screenshot that opens past the first step. */
const SEEDED_ANSWERS: FindAnswers = {
  goal: "better-days",
  wake: "06:30",
  caffeine: "morning",
  treatments: [],
  devices: ["rayban-meta"],
  quiet: "22:00-06:30",
};

export interface FindFlowProps {
  /** Fixtures mode only: the step to open on (1 to 6, or 7 for the result), with seeded answers. */
  start?: number;
  /** Fixtures mode only: pins the appearance for screenshots. */
  theme?: "light" | "dark";
  /** Fixtures mode only: text size multiplier. */
  scale?: number;
}

/** A step's answers: from the content, or the Treatments and Devices catalogues. */
function optionsFor(step: FindStep): StepOption[] {
  if (step.options) return step.options;
  if (step.id === "treatments") return TREATMENTS.map((treatment) => ({ id: treatment.id, label: treatment.name, icon: treatment.icon }));
  if (step.id === "devices") return DEVICES.map((device) => ({ id: device.id, label: device.name, icon: device.icon }));
  return [];
}

const first = (value: string | string[] | undefined): string | undefined => (Array.isArray(value) ? value[0] : value);

/** "21:30-07:00" becomes the concierge's quiet hours; anything else leaves them alone. */
function saveQuietHours(answer: string | string[] | undefined): void {
  const [quietStart, quietEnd] = (first(answer) ?? "").split("-");
  if (!quietStart || !quietEnd) return;
  writeConcierge({ ...readConcierge(), quietStart, quietEnd });
}

/**
 * Find my protocol: six full-screen questions, each answered with pill rows,
 * then the closest template shifted to the wearer's wake as the guided review.
 * Answers live in this component only; "Save to My protocol" writes the
 * protocol (the shared store) and the quiet hours (the concierge's store),
 * then the flow opens Protocol.
 */
export function FindFlow({ start, theme, scale }: FindFlowProps) {
  const router = useRouter();
  const [index, setIndex] = useState(() => (start ? Math.min(TOTAL, Math.max(0, start - 1)) : 0));
  const [answers, setAnswers] = useState<FindAnswers>(() => (start !== undefined && start > 1 ? SEEDED_ANSWERS : {}));

  const review = index >= TOTAL;
  const step = FIND_STEPS[Math.min(index, TOTAL - 1)];
  const wake = first(answers.wake) ?? DEFAULT_WAKE;
  const templateId = pickTemplate(answers);
  // The treatments the wearer said they take ride along as review toggles, after the template's own items.
  const pickedKey = (Array.isArray(answers.treatments) ? answers.treatments : answers.treatments ? [answers.treatments] : []).join(",");
  const template = useMemo(() => {
    const shifted = shiftWindows(templateId, wake);
    if (!shifted) return shifted;
    const chosen = new Set(pickedKey ? pickedKey.split(",") : []);
    const have = new Set(shifted.items.map((item) => item.id));
    const extra = TREATMENTS.filter((treatment) => chosen.has(treatment.id) && !have.has(treatment.item.id)).map(
      (treatment) => treatment.item,
    );
    return extra.length ? { ...shifted, items: [...shifted.items, ...extra] } : shifted;
  }, [templateId, wake, pickedKey]);

  const back = () => {
    if (index > 0) {
      setIndex(index - 1);
      return;
    }
    if (window.history.length > 1) router.back();
    else router.push("/");
  };
  const next = () => setIndex(Math.min(TOTAL, index + 1));
  const restart = () => {
    setAnswers({});
    setIndex(0);
  };
  const done = () => {
    saveQuietHours(answers.quiet);
    router.push("/protocol");
  };

  if (review) {
    const original = protocolById(templateId);
    const base = original ? templateWake(original) : null;
    const moved = original !== undefined && !original.flags.anchorToActualSleep && base !== null && base !== wake;
    return (
      <FindFrame step={TOTAL + 1} total={TOTAL} title="Your protocol" onBack={back} theme={theme} scale={scale}>
        {template ? (
          <>
            <p className="type-secondary m-0 mt-2 text-muted tabular-nums">
              {moved ? `From ${template.name}, moved to a ${wake} wake` : `From ${template.name}`}
            </p>
            <ProtocolReview template={template} source="find" onDone={done} />
          </>
        ) : (
          <ErrorState
            sentence="No template matched these answers. Start over to try again."
            action={<Button onClick={restart}>Start over</Button>}
          />
        )}
      </FindFrame>
    );
  }

  return (
    <FindFrame step={index + 1} total={TOTAL} title={step.question} onBack={back} theme={theme} scale={scale}>
      <StepScreen
        key={step.id}
        step={step}
        options={optionsFor(step)}
        value={answers[step.id]}
        onChange={(value) => setAnswers((previous) => ({ ...previous, [step.id]: value }))}
        onContinue={next}
      />
    </FindFrame>
  );
}
