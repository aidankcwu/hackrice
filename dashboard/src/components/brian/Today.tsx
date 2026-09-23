"use client";
import { useId, useState } from "react";
import { withBasePath } from "@/lib/runtime";
import { T, fmtH, tone } from "@/lib/tokens";
import type { CurrenciesView, DashboardData, ExperienceView } from "@/lib/score/types";
import { fmtHoursValue, fmtSigned } from "./format";
import { Panel } from "./Panel";

export interface TodayProps {
  d: DashboardData;
  /** A fresh payload is on its way; shown quietly on the label line so nothing moves. */
  updating?: boolean;
}

/* Ledger card — screens.md §1.1. The hero number, the sentence with its likely
   range, then the two currencies side by side: fully-lived hours (how well today
   was lived) and future healthy years (what today implies for the rest of it).
   Both currencies are absent rather than zeroed when the day carries nothing
   that measures them. */

/** The four things `utility_today` weighs, in the order the engine lists them. */
const COMPONENT_LABELS: ReadonlyArray<[key: keyof ExperienceView["components"], label: string]> = [
  ["pvt", "reaction time"],
  ["check", "self-check"],
  ["recovery", "WHOOP recovery"],
  ["pain_or_illness", "illness"],
];

/**
 * The popover listing what went into fully-lived hours. A component nobody
 * measured reads the voice.md unmeasured string, because the engine counted it
 * as nothing and the popover must say so rather than omit the row.
 */
function Components({ experience }: { experience: ExperienceView }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {COMPONENT_LABELS.map(([key, label]) => {
        const raw = experience.components[key];
        const text =
          key === "pain_or_illness"
            ? raw === true
              ? "reported today"
              : "none reported"
            : typeof raw === "number"
              ? raw.toFixed(2)
              : "Unmeasured today — scored at the population average, earns nothing.";
        return (
          <li key={key} className="flex flex-wrap items-baseline justify-between gap-x-3" style={{ fontSize: 12 }}>
            <span style={{ color: T.text }}>{label}</span>
            <span style={{ color: T.muted }}>{text}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** One of the two currencies: the big number, its caption, and an optional popover. */
function Currency({
  title,
  value,
  caption,
  popover,
}: {
  title: string;
  value: string;
  caption: string;
  popover?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div className="min-w-0 flex-1">
      <div className="text-sm" style={{ color: T.muted }}>
        {title}
      </div>
      <div className="tnum font-extrabold leading-none" style={{ color: T.ink, fontSize: 40 }}>
        {value}
      </div>
      <p className="m-0 mt-1" style={{ color: T.muted, fontSize: 12 }}>
        {caption}
      </p>
      {popover && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            aria-expanded={open}
            aria-controls={panelId}
            className="mt-2 inline-flex min-h-6 items-center rounded-full text-sm font-medium underline decoration-1 underline-offset-2"
            style={{ color: T.text }}
          >
            What goes into this
          </button>
          <div
            id={panelId}
            hidden={!open}
            className="mt-2 rounded-pin p-3"
            style={{ background: T.bg, borderRadius: 12 }}
          >
            {popover}
          </div>
        </>
      )}
    </div>
  );
}

/** `+3.4` / `−0.2`, the years currency, with the CI the caption quotes. */
const yearsCaption = (age: number, ci: readonly [number, number]): string =>
  `vs a typical ${age}-year-old · ${fmtSigned(ci[0], 1)} to ${fmtSigned(ci[1], 1)}`;

export function Today({ d, updating = false }: TodayProps) {
  const cost = d.hours_today < -0.05;
  const experience = d.experience ?? null;
  const currencies: CurrenciesView | null = d.currencies ?? null;
  // The years number the engine scored today stands in when the two-currency
  // object has not reached this payload — it is the same quantity, unweighted by
  // utility, and `years_ci` is its own interval.
  const years = currencies?.future_healthy_years ?? d.years_delta;
  const yearsCi = currencies?.future_healthy_years_ci ?? d.years_ci;

  return (
    <Panel id="today" labelledBy="today-title" className="md:col-span-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="today-title" className="m-0 text-sm font-medium" style={{ color: T.muted }}>
          Today
        </h2>
        <span className="text-sm" style={{ color: T.muted }} aria-live="polite">
          {updating ? "Scoring today…" : ""}
        </span>
      </div>

      <p
        className="tnum m-0 mt-2 font-extrabold leading-none"
        style={{ fontSize: 72, letterSpacing: "-0.02em", color: tone(d.hours_today) }}
      >
        {fmtHoursValue(d.hours_today)} h
      </p>
      <p className="m-0 mt-3" style={{ color: T.text, fontSize: 16 }}>
        of healthy life {cost ? "cost" : "earned"} today. Likely range {fmtH(d.hours_ci[0])} to{" "}
        {fmtH(d.hours_ci[1])}.
      </p>

      <div className="mt-6 flex flex-wrap gap-6 pt-5" style={{ borderTop: `1px solid ${T.line}` }}>
        <Currency
          title="Fully-lived hours"
          value={experience === null ? "—" : `${experience.fully_lived_hours.toFixed(1)} / 24`}
          caption={
            experience === null
              ? "Unmeasured today — scored at the population average, earns nothing."
              : "how well you lived today, not how long"
          }
          popover={experience === null ? undefined : <Components experience={experience} />}
        />
        <Currency
          title="Future healthy years"
          value={fmtSigned(years, 1)}
          caption={yearsCaption(d.person.age, yearsCi)}
        />
      </div>

      <p className="m-0 mt-5" style={{ color: T.muted, fontSize: 12 }}>
        Healthspan score {Math.round(d.overall)} / 100 ·{" "}
        <a href={withBasePath("/how-its-scored")} className="underline decoration-1 underline-offset-2" style={{ color: T.muted }}>
          How the hours are computed →
        </a>
      </p>
    </Panel>
  );
}
