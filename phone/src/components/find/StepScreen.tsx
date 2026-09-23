"use client";

import { Button } from "@/components/ui";
import type { FindStep, IconName } from "@/content/types";
import { OptionPill } from "./OptionPill";

export interface StepOption {
  id: string;
  label: string;
  icon: IconName;
}

export interface StepScreenProps {
  step: FindStep;
  options: StepOption[];
  /** One id on a single-select step, a list of ids on a multi-select step, undefined before any tap. */
  value: string | string[] | undefined;
  onChange: (value: string | string[]) => void;
  onContinue: () => void;
}

/** Clearance under the list for the fixed Continue bar: 12 above the button, 48, 16 under, and 20 clear. */
const CONTINUE_CLEARANCE = "calc(96px + env(safe-area-inset-bottom))";

/**
 * One question's answers as pill rows, single or multi select, and Continue
 * pinned above the home indicator. A single-select step needs an answer before
 * Continue enables; a multi-select step may continue with none picked.
 */
export function StepScreen({ step, options, value, onChange, onContinue }: StepScreenProps) {
  const selected = new Set(Array.isArray(value) ? value : value === undefined ? [] : [value]);
  const canContinue = Boolean(step.multi) || selected.size > 0;

  const pick = (id: string) => {
    if (!step.multi) {
      onChange(id);
      return;
    }
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    // Kept in catalogue order, whatever the order of the taps.
    onChange(options.filter((option) => next.has(option.id)).map((option) => option.id));
  };

  return (
    <>
      <ul aria-label={step.question} className="m-0 mt-section flex list-none flex-col gap-2 p-0" style={{ paddingBottom: CONTINUE_CLEARANCE }}>
        {options.map((option) => (
          <OptionPill
            key={option.id}
            icon={option.icon}
            label={option.label}
            selected={selected.has(option.id)}
            onSelect={() => pick(option.id)}
          />
        ))}
      </ul>

      <div className="fixed inset-x-0 bottom-0 z-10 bg-page" style={{ paddingBottom: "calc(16px + env(safe-area-inset-bottom))" }}>
        <div className="mx-auto max-w-[430px] px-gutter pt-3">
          <Button onClick={onContinue} disabled={!canContinue}>
            Continue
          </Button>
        </div>
      </div>
    </>
  );
}
