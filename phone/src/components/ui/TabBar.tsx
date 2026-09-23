import Link from "next/link";
import { CalendarDays, ChartNoAxesColumn, ListChecks, Sun, type LucideIcon } from "lucide-react";
import { SCREENS, TABS, type ScreenId } from "@/lib/screens";
import { STROKE } from "./icons";

export interface TabBarProps {
  active: ScreenId;
}

/** One icon per tab. */
const TAB_ICONS: Partial<Record<ScreenId, LucideIcon>> = {
  today: Sun,
  calendar: CalendarDays,
  analysis: ChartNoAxesColumn,
  protocol: ListChecks,
};

/** The floating tab pill: 64 px tall, 12 px in from the edges, 8 px above the home indicator. */
export function TabBar({ active }: TabBarProps) {
  return (
    <nav
      aria-label="Tabs"
      className="pointer-events-none fixed inset-x-0 z-30 mx-auto max-w-[430px] px-3"
      style={{ bottom: "calc(8px + env(safe-area-inset-bottom))" }}
    >
      <div className="glass pointer-events-auto flex h-16 rounded-full">
        {TABS.map((id) => {
          const on = id === active;
          const Icon = TAB_ICONS[id] ?? ListChecks;
          return (
            <Link
              key={id}
              href={SCREENS[id].href}
              aria-current={on ? "page" : undefined}
              className={`flex min-h-11 flex-1 flex-col items-center justify-center gap-1 rounded-full transition-colors duration-200 ${
                on ? "text-ink" : "text-muted"
              }`}
            >
              <Icon size={22} strokeWidth={STROKE} aria-hidden="true" />
              <span className="type-tab">{SCREENS[id].title}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
