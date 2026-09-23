"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { Check } from "lucide-react";
import { Shell } from "@/components/Shell";
import { FAMILY_ICONS } from "@/components/today/icons";
import { api, type ApiError } from "@/lib/api";
import { KINDS, WEEKDAYS } from "@/lib/protocol";
import { errorSentence, toApiError } from "@/lib/today";
import type { ProtocolKind } from "@/lib/types";
import { PRIMARY } from "./Protocol";

const ROW = "flex min-h-[52px] items-center gap-4 border-t-[0.5px] border-line py-2";
const GROUP = "m-0 border-b-[0.5px] border-line p-0";
const HEADING = "type-secondary m-0 mb-2 text-muted";

/**
 * AddItemView, pushed from Protocol's "+": name, kind, window start and end, and
 * weekday toggles → `POST /api/protocol`, then back to the list. "Add item" waits
 * for a name and a window that ends after it starts, the same rules the backend
 * checks, so a bad field never leaves the phone.
 */
export function AddItem({ query, theme, scale }: { query: string; theme?: "light" | "dark"; scale?: number }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProtocolKind>("dose");
  // The first seeded item's window (docs/API.md "The protocol": Morning dose 07:00–10:00).
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
      if (window.history.length > 1) router.back();
      else router.push(`/protocol${query}`);
    } catch (reason) {
      setError(toApiError(reason));
      setSaving(false);
    }
  };

  return (
    <Shell screen="protocol" pushed title="Add item" theme={theme} scale={scale}>
      <form onSubmit={submit} noValidate className="mt-2">
        <div className={GROUP}>
          <label className={ROW}>
            <span className="type-body shrink-0 text-text">Name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Morning dose"
              autoComplete="off"
              enterKeyHint="done"
              required
              className="type-body min-w-0 flex-1 bg-transparent text-right text-text placeholder:text-muted focus:outline-none"
            />
          </label>
        </div>

        <div role="radiogroup" aria-labelledby="kind-label" className="mt-6">
          <p id="kind-label" className={HEADING}>
            Kind
          </p>
          <div className={GROUP}>
            {KINDS.map((entry) => {
              const Icon = FAMILY_ICONS[entry.family];
              const on = kind === entry.kind;
              return (
                <label key={entry.kind} className={`${ROW} cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ink`}>
                  <input
                    type="radio"
                    name="kind"
                    value={entry.kind}
                    checked={on}
                    onChange={() => setKind(entry.kind)}
                    className="sr-only"
                  />
                  <span className="flex w-[calc(18px*var(--type-scale))] shrink-0 justify-center text-muted">
                    <Icon className="size-[calc(18px*var(--type-scale))]" strokeWidth={2} aria-hidden="true" />
                  </span>
                  <span className="type-body min-w-0 flex-1 text-text">{entry.label}</span>
                  {on ? (
                    <Check className="size-[calc(20px*var(--type-scale))] shrink-0 text-ink" strokeWidth={2.25} aria-hidden="true" />
                  ) : null}
                </label>
              );
            })}
          </div>
        </div>

        <div className={`${GROUP} mt-6`}>
          <TimeRow label="Start" value={start} onChange={setStart} />
          <TimeRow label="End" value={end} onChange={setEnd} invalid={endsFirst} />
        </div>

        <div role="group" aria-labelledby="days-label" className="mt-6">
          <p id="days-label" className={HEADING}>
            Days
          </p>
          <div className="flex flex-wrap justify-between gap-1">
            {WEEKDAYS.map(({ day, letter, name: dayName }) => {
              const on = days.includes(day);
              return (
                <label
                  key={day}
                  className={`type-body grid min-h-11 min-w-11 cursor-pointer place-items-center rounded-full has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ink ${
                    on ? "bg-surface-2 font-semibold text-ink" : "border-[0.5px] border-line text-muted"
                  }`}
                >
                  <input type="checkbox" checked={on} onChange={() => toggleDay(day)} className="sr-only" />
                  <span aria-hidden="true">{letter}</span>
                  <span className="sr-only">{dayName}</span>
                </label>
              );
            })}
          </div>
        </div>

        {error ? (
          <p role="alert" className="type-secondary m-0 mt-section text-cost">
            {errorSentence(error)}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={!ready || saving}
          className={`${PRIMARY} ${error ? "mt-4" : "mt-section"} disabled:opacity-40`}
        >
          {error ? "Try again" : "Add item"}
        </button>
      </form>
    </Shell>
  );
}

function TimeRow({
  label,
  value,
  onChange,
  invalid = false,
}: {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  invalid?: boolean;
}) {
  return (
    <label className={ROW}>
      <span className="type-body min-w-0 flex-1 text-text">{label}</span>
      <input
        type="time"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
        aria-invalid={invalid || undefined}
        className={`type-body bg-transparent text-right text-text tabular-nums focus:outline-none ${
          invalid ? "line-through" : ""
        }`}
      />
    </label>
  );
}
