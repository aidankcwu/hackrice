"use client";

import { useState, type FormEvent } from "react";
import { Button, Field, Sheet } from "@/components/ui";
import { api, type ApiError } from "@/lib/api";
import { KINDS, WEEKDAYS } from "@/lib/protocol";
import { errorSentence, toApiError } from "@/lib/today";
import type { ProtocolKind } from "@/lib/types";

const ROW = "flex min-h-row items-center gap-4";
const LABEL = "type-body shrink-0 text-text";
const PILL_BASE =
  "type-chip relative grid min-h-7 cursor-pointer place-items-center rounded-full px-3 motion pressable before:absolute before:inset-x-0 before:-inset-y-2 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ink";
const PILL_ON = "bg-ink text-page";
const PILL_OFF = "bg-surface-2 text-text";

export interface AddItemSheetProps {
  open: boolean;
  onClose: () => void;
  /** After a successful `POST /api/protocol`. */
  onAdded: () => void;
}

/**
 * Add item, a sheet from Protocol's "+": name, kind, window start and end, and
 * weekday toggles → `POST /api/protocol`. The primary button waits for a name
 * and a window that ends after it starts, the same rules the backend checks,
 * so a bad field never leaves the phone.
 */
export function AddItemSheet({ open, onClose, onAdded }: AddItemSheetProps) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProtocolKind>("dose");
  // The first seeded item's window (docs/API.md "The protocol": Morning dose 07:00 to 10:00).
  const [start, setStart] = useState("07:00");
  const [end, setEnd] = useState("10:00");
  const [days, setDays] = useState<number[]>(WEEKDAYS.map(({ day }) => day));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const endsFirst = Boolean(start && end) && end <= start;
  const ready = name.trim() !== "" && start !== "" && end !== "" && !endsFirst && days.length > 0;

  const toggleDay = (day: number) =>
    setDays((current) => (current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort((a, b) => a - b)));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    setError(null);
    try {
      await api.addProtocolItem({ name: name.trim(), kind, window_start: start, window_end: end, days });
      setSaving(false);
      setName("");
      onAdded();
    } catch (reason) {
      setError(toApiError(reason));
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Add item" id="add-item">
      <form onSubmit={submit} noValidate className="mt-2">
        <Field id="add-item-name" label="Name" value={name} onChange={setName} placeholder="Morning dose" />

        <div role="radiogroup" aria-labelledby="add-item-kind" className={`${ROW} hairline-b`}>
          <span id="add-item-kind" className={LABEL}>
            Kind
          </span>
          <div className="flex min-w-0 flex-1 flex-wrap justify-end gap-2 py-2">
            {KINDS.map((entry) => {
              const on = kind === entry.kind;
              return (
                <label key={entry.kind} className={`${PILL_BASE} ${on ? PILL_ON : PILL_OFF}`}>
                  <input type="radio" name="kind" value={entry.kind} checked={on} onChange={() => setKind(entry.kind)} className="sr-only" />
                  {entry.label}
                </label>
              );
            })}
          </div>
        </div>

        <Field id="add-item-start" label="Start" type="time" value={start} onChange={setStart} />
        <Field id="add-item-end" label="End" type="time" value={end} onChange={setEnd} />
        {endsFirst ? <p className="type-caption m-0 mt-2 text-bad">End is before start.</p> : null}

        <div role="group" aria-labelledby="add-item-days" className={ROW}>
          <span id="add-item-days" className={LABEL}>
            Days
          </span>
          <div className="flex min-w-0 flex-1 justify-end gap-1 py-2">
            {WEEKDAYS.map(({ day, letter, name: dayName }) => {
              const on = days.includes(day);
              return (
                <label key={day} className={`${PILL_BASE} size-9 px-0! before:-inset-1 ${on ? PILL_ON : PILL_OFF}`}>
                  <input type="checkbox" checked={on} onChange={() => toggleDay(day)} className="sr-only" />
                  <span aria-hidden="true">{letter}</span>
                  <span className="sr-only">{dayName}</span>
                </label>
              );
            })}
          </div>
        </div>

        {error ? (
          <p role="alert" className="type-secondary m-0 mt-4 text-bad">
            {errorSentence(error)}
          </p>
        ) : null}
        <Button type="submit" disabled={!ready || saving} className="mt-section">
          {error ? "Try again" : "Add item"}
        </Button>
      </form>
    </Sheet>
  );
}
