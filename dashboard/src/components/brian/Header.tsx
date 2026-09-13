"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { T } from "@/lib/tokens";
import { GOALS } from "@/lib/score/types";
import type { DataSource, Goal, Person } from "@/lib/score/types";

export interface BrianHeaderProps {
  person: Person;
  source: DataSource;
  goal: Goal;
  onGoalChange?: (goal: Goal) => void;
  /** Which top-level view is showing, for `aria-current`. */
  active?: "today" | "factors";
}

/** In-page sections, in page order; the ids live on the panels in BrianDashboard. */
const SECTIONS = [
  { id: "today", label: "Today" },
  { id: "week", label: "Week" },
  { id: "evidence", label: "Evidence" },
  { id: "plan", label: "Plan" },
] as const;

const isGoal = (v: string): v is Goal => GOALS.some((g) => g.value === v);

function sourceChip(source: DataSource): string {
  if (source.mode !== "live") return "backend offline · nothing measured";
  const parts = ["live"];
  if (source.capture_source) parts.push(source.capture_source);
  if (source.tick_count !== undefined) parts.push(`${source.tick_count.toLocaleString("en-US")} ticks`);
  return parts.join(" · ");
}

const PILL = "inline-flex items-center h-11 px-4 rounded-full text-sm font-medium no-underline";
const pillStyle = (on: boolean) => ({ color: on ? T.header : T.bg, background: on ? T.bg : "transparent" });

export function BrianHeader({ person, source, goal, onGoalChange, active = "today" }: BrianHeaderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const onDashboard = active !== "factors";

  // The hash is not part of the server render, so it is read after mount;
  // the click handler updates it too because a same-page anchor navigation
  // does not always fire `hashchange`.
  const [hash, setHash] = useState("");
  useEffect(() => {
    const read = () => setHash(window.location.hash.replace(/^#/, ""));
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const currentSection = onDashboard ? hash || "today" : "";

  const changeGoal = (value: string) => {
    if (!isGoal(value)) return;
    if (onGoalChange) onGoalChange(value);
    else router.push(`${pathname}?goal=${value}`);
  };

  return (
    <header style={{ background: T.header }}>
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6 md:min-h-16">
        <h1 className="m-0 text-2xl font-extrabold leading-none tracking-tight" style={{ color: T.bg }}>
          BRYAN
        </h1>

        <nav
          aria-label="Sections"
          className="order-last flex w-full flex-wrap items-center gap-1 md:order-none md:mx-auto md:w-auto"
        >
          {SECTIONS.map((s) => {
            const on = currentSection === s.id;
            return onDashboard ? (
              <a
                key={s.id}
                href={`#${s.id}`}
                className={PILL}
                style={pillStyle(on)}
                aria-current={on ? (hash ? "location" : "page") : undefined}
                onClick={() => setHash(s.id)}
              >
                {s.label}
              </a>
            ) : (
              <Link key={s.id} href={`/#${s.id}`} className={PILL} style={pillStyle(false)}>
                {s.label}
              </Link>
            );
          })}
          <Link
            href="/factors"
            className={PILL}
            style={pillStyle(!onDashboard)}
            aria-current={!onDashboard ? "page" : undefined}
          >
            Factors
          </Link>
        </nav>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
          <p className="m-0 text-sm" style={{ color: "rgba(255,255,255,0.8)" }}>
            {person.name} · {person.age} · {person.profileLabel}
          </p>
          <span
            className="inline-flex h-7 items-center rounded-full px-3 text-xs font-medium"
            style={{ background: "rgba(255,255,255,0.1)", color: "rgba(255,255,255,0.8)" }}
          >
            {sourceChip(source)}
          </span>
          <label className="flex items-center gap-2 text-sm" style={{ color: T.bg }}>
            Profile
            <select
              value={goal}
              onChange={(e) => changeGoal(e.target.value)}
              className="h-11 rounded-full px-3 text-sm font-medium"
              style={{ background: T.bg, color: T.ink, border: 0 }}
            >
              {GOALS.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
    </header>
  );
}
