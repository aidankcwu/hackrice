"use client";

import { useState, type FormEvent } from "react";
import { Button, Field, Sheet } from "@/components/ui";
import type { BiomarkerCard } from "@/content/biomarkers";
import { isIsoDay, parseValue, todayIso, type BiomarkerResult } from "@/lib/biomarkerStore";

export interface AddResultSheetProps {
  /** The marker a result is being added for; null keeps the sheet closed. */
  biomarker: BiomarkerCard | null;
  onSave: (result: BiomarkerResult) => void;
  onClose: () => void;
}

/**
 * The "Add result" sheet: the marker's name as the title, a value field and a
 * date field, and Save, which stays disabled until the value parses. An empty
 * date saves as today.
 */
export function AddResultSheet({ biomarker, onSave, onClose }: AddResultSheetProps) {
  return (
    <Sheet open={biomarker !== null} onClose={onClose} title={biomarker?.name} id="add-result">
      {/* Keyed by marker so each opening starts with empty fields. */}
      {biomarker ? <ResultForm key={biomarker.id} biomarker={biomarker} onSave={onSave} /> : null}
    </Sheet>
  );
}

function ResultForm({ biomarker, onSave }: { biomarker: BiomarkerCard; onSave: (result: BiomarkerResult) => void }) {
  const [value, setValue] = useState("");
  const [date, setDate] = useState("");

  const parsed = parseValue(value);
  const day = date.trim();
  const dateOk = day === "" || isIsoDay(day);
  const canSave = parsed !== null && dateOk;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (parsed === null || !dateOk) return;
    onSave({ id: biomarker.id, value: parsed, date: day || todayIso() });
  };

  return (
    <form onSubmit={submit} className="mt-2">
      <p className="type-secondary m-0 text-center text-muted">
        In {biomarker.unit}. Target {biomarker.target}.
      </p>
      {biomarker.note ? <p className="type-caption m-0 mt-1 text-center text-muted tabular-nums">{biomarker.note}</p> : null}
      <div className="mt-4">
        <Field id="add-result-value" label="Value" value={value} onChange={setValue} placeholder={biomarker.unit} />
        <Field id="add-result-date" label="Date" value={date} onChange={setDate} placeholder="YYYY-MM-DD" />
      </div>
      <p className="type-caption m-0 mt-3 text-muted">Leave the date empty for today.</p>
      <div className="mt-4">
        <Button type="submit" disabled={!canSave}>
          Save
        </Button>
      </div>
    </form>
  );
}
