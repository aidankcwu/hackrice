"use client";

import { Droplet, Sun, Users, type LucideIcon } from "lucide-react";
import { sunTimes } from "@/lib/month/sun";
import { HOUSTON, WINDOWS, daylightMinutes, peopleMinutes, waterMl } from "@/lib/rules";
import { useMonth } from "@/lib/useMonth";

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
 * time with people, daylight. Each a row with its progress. Absent when the
 * backend serves no month.
 */
export function DailyAmounts() {
  const month = useMonth();
  const day = month.month?.days.at(-1);
  if (!day) return null;

  const sun = sunTimes(day.date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);
  const water = waterMl(day);
  const people = peopleMinutes(day);
  const daylight = daylightMinutes(day, sun.sunset);
  const rows: Amount[] = [
    { id: "water", icon: Droplet, name: "Water", target: "2 L by 18:00", value: `${(water / 1000).toFixed(1)} of 2 L`, done: water / WINDOWS.waterTarget },
    { id: "people", icon: Users, name: "People", target: "30 min face to face by 21:00", value: `${people} of 30 min`, done: people / WINDOWS.peopleTarget },
    { id: "daylight", icon: Sun, name: "Daylight", target: "60 min outside by sunset", value: `${daylight} of 60 min`, done: daylight / WINDOWS.daylightTarget },
  ];

  return (
    <section aria-label="Daily amounts" className="mt-section">
      <h2 className="type-title m-0 text-ink">Daily amounts</h2>
      <ul className="m-0 mt-2 list-none border-b-[0.5px] border-line p-0">
        {rows.map((row) => {
          const Icon = row.icon;
          const pct = Math.round(Math.min(1, row.done) * 100);
          const met = row.done >= 1;
          return (
            <li key={row.id} className="border-t-[0.5px] border-line py-3">
              <div className="flex items-center">
                <span className="flex w-[calc(18px*var(--type-scale))] shrink-0 justify-center text-muted">
                  <Icon className="size-[calc(18px*var(--type-scale))]" strokeWidth={2} aria-hidden="true" />
                </span>
                <span className="ml-4 min-w-0 flex-1">
                  <span className="type-body block text-text">{row.name}</span>
                  <span className="type-secondary block text-muted">{row.target}</span>
                </span>
                <span className={`type-outcome whitespace-nowrap tabular-nums ${met ? "text-earn" : "text-ink"}`}>{row.value}</span>
              </div>
              <div
                role="progressbar"
                aria-label={`${row.name}, ${row.value}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct}
                className="mt-2 ml-[calc(18px*var(--type-scale)+16px)] h-1.5 overflow-hidden rounded-full bg-band"
              >
                <div className={`h-full rounded-full ${met ? "bg-earn" : "bg-muted"}`} style={{ width: `${pct}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
