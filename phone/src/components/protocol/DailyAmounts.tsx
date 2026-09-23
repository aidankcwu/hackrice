"use client";

import type { LucideIcon } from "lucide-react";
import { ICON_SIZES, InsetList, MEANING_ICONS, ProgressBar, STROKE } from "@/components/ui";
import type { Day } from "@/lib/month/types";
import { sunTimes } from "@/lib/month/sun";
import { HOUSTON, WINDOWS, daylightMinutes, peopleMinutes, waterMl } from "@/lib/rules";

interface Amount {
  id: string;
  icon: LucideIcon;
  name: string;
  target: string;
  value: string;
  done: number;
}

/**
 * The things with a daily amount but no meaningful time, for today: water,
 * time with people, daylight. Each a 56 px row with its amount at the right
 * and a 6 px progress bar under. Absent when the backend serves no month.
 */
export function DailyAmounts({ day }: { day: Day | null | undefined }) {
  if (!day) return null;

  const sun = sunTimes(day.date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);
  const water = waterMl(day);
  const people = peopleMinutes(day);
  const daylight = daylightMinutes(day, sun.sunset);
  const rows: Amount[] = [
    {
      id: "water",
      icon: MEANING_ICONS.water,
      name: "Water",
      target: "2 L by 18:00",
      value: `${(water / 1000).toFixed(1)} of 2 L`,
      done: water / WINDOWS.waterTarget,
    },
    {
      id: "people",
      icon: MEANING_ICONS.people,
      name: "People",
      target: "30 min face to face by 21:00",
      value: `${people} of 30 min`,
      done: people / WINDOWS.peopleTarget,
    },
    {
      id: "daylight",
      icon: MEANING_ICONS.light,
      name: "Daylight",
      target: "60 min outside by sunset",
      value: `${daylight} of 60 min`,
      done: daylight / WINDOWS.daylightTarget,
    },
  ];

  return (
    <section aria-labelledby="daily-amounts" className="mt-section">
      <h2 id="daily-amounts" className="type-section m-0 text-ink">
        Daily amounts
      </h2>
      <div className="mt-3">
        <InsetList>
          {rows.map((row) => {
            const Icon = row.icon;
            const met = row.done >= 1;
            return (
              <li
                key={row.id}
                className="relative flex min-h-row items-center gap-2 py-2 pr-4 pl-3 not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline"
              >
                <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center self-start rounded-full bg-muted/20 text-muted">
                  <Icon size={ICON_SIZES.list} strokeWidth={STROKE} />
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="type-body text-text">{row.name}</span>
                    <span className={`type-caption shrink-0 tabular-nums ${met ? "text-good" : "text-muted"}`}>{row.value}</span>
                  </span>
                  <span className="type-secondary text-muted">{row.target}</span>
                  <span className="mt-2 block">
                    <ProgressBar value={row.done} label={`${row.name}, ${row.value}`} />
                  </span>
                </span>
              </li>
            );
          })}
        </InsetList>
      </div>
    </section>
  );
}
