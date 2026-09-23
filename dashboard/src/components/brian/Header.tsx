"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Brain, Calendar, Camera, Home, Scale } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { T } from "@/lib/tokens";
import type { DataSource, Person } from "@/lib/score/types";

export interface BrianHeaderProps {
  person: Person;
  source: DataSource;
  /** Opens the Profile sheet; absent until the profile lane lands it. */
  onOpenProfile?: () => void;
}

/**
 * The five pages, in page order, with the route each pill owns (screens.md §0).
 * Only `/` exists today; the other four are built by later lanes and are linked
 * regardless, so the nav is complete the moment each page lands.
 */
const TABS: ReadonlyArray<{ href: string; label: string; icon: LucideIcon }> = [
  { href: "/", label: "Today", icon: Home },
  { href: "/week", label: "Week", icon: Calendar },
  { href: "/evidence", label: "Evidence", icon: Camera },
  { href: "/plan", label: "Plan", icon: Brain },
  { href: "/how-its-scored", label: "How it's scored", icon: Scale },
];

/** A tick older than this is history, not a live stream (screens.md §0). */
const LIVE_WINDOW_S = 60;

const isActive = (pathname: string, href: string): boolean =>
  href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);

/**
 * Green "Live" only when a glasses tick actually arrived inside the last
 * 60 seconds; grey "Seeded" otherwise. Computed after mount because the
 * comparison needs the reader's clock, and a server render cannot know how long
 * the HTML sat in transit — the dot would otherwise claim a liveness the stream
 * has not earned (R1).
 */
function useLive(source: DataSource): boolean {
  const [live, setLive] = useState(false);
  const lastTick = source.last_tick_t;
  useEffect(() => {
    if (lastTick === undefined) {
      setLive(false);
      return;
    }
    const read = () => setLive(Date.now() / 1000 - lastTick < LIVE_WINDOW_S);
    read();
    // The dot has to go grey on its own when the stream stops, not wait for the
    // next payload — a stalled poll would otherwise freeze it on green.
    const timer = window.setInterval(read, 5000);
    return () => window.clearInterval(timer);
  }, [lastTick]);
  return live;
}

function StatusDot({ live }: { live: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm" style={{ color: "rgba(255,255,255,0.8)" }}>
      <span
        aria-hidden="true"
        className="block shrink-0 rounded-full"
        style={{ width: 8, height: 8, background: live ? T.earn : "#8A8A92" }}
      />
      {live ? "Live" : "Seeded"}
    </span>
  );
}

/** `Bryan · 20 · Average` — the wearer, their age, the goal driving the targets. */
function ProfileChip({ person, onOpenProfile }: { person: Person; onOpenProfile?: () => void }) {
  const label = `${person.name} · ${person.age} · ${person.profileLabel}`;
  const className = "inline-flex h-11 items-center rounded-full px-4 text-sm font-medium";
  const style = { background: "rgba(255,255,255,0.12)", color: T.bg };
  return onOpenProfile ? (
    <button type="button" onClick={onOpenProfile} className={className} style={style} aria-haspopup="dialog">
      {label}
    </button>
  ) : (
    <span className={className} style={style}>
      {label}
    </span>
  );
}

export function BrianHeader({ person, source, onOpenProfile }: BrianHeaderProps) {
  const pathname = usePathname() ?? "/";
  const live = useLive(source);

  return (
    <>
      <header style={{ background: T.header }}>
        <div className="mx-auto flex min-h-16 max-w-[1200px] items-center gap-4 px-4 sm:px-6">
          <Link
            href="/"
            className="m-0 shrink-0 font-extrabold leading-none no-underline"
            style={{ color: T.bg, fontSize: 24, letterSpacing: "-0.02em" }}
          >
            BRYAN
          </Link>

          {/* Desktop tabs. On phones the same five items are the bottom bar
              below, so this nav is hidden there rather than wrapped. */}
          <nav aria-label="Pages" className="mx-auto hidden items-center gap-1 md:flex">
            {TABS.map((tab) => {
              const on = isActive(pathname, tab.href);
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={on ? "page" : undefined}
                  className="inline-flex h-11 items-center rounded-full px-4 text-sm font-medium whitespace-nowrap no-underline"
                  style={{
                    color: on ? T.ink : "rgba(255,255,255,0.82)",
                    background: on ? T.bg : "transparent",
                  }}
                >
                  {tab.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-3 sm:gap-4">
            <StatusDot live={live} />
            <ProfileChip person={person} onOpenProfile={onOpenProfile} />
          </div>
        </div>
      </header>

      {/* Mobile: the five pages as a fixed bottom bar, icons over 12px labels,
          56px tall plus the home-indicator inset. */}
      <nav
        aria-label="Pages"
        className="tabbar fixed bottom-0 left-0 z-40 flex w-full md:hidden"
        style={{ background: T.header }}
      >
        {TABS.map((tab) => {
          const on = isActive(pathname, tab.href);
          const TabIcon = tab.icon;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={on ? "page" : undefined}
              className="flex min-w-0 flex-1 flex-col items-center justify-center gap-1 no-underline"
              style={{ height: 56, color: on ? T.bg : "rgba(255,255,255,0.62)" }}
            >
              <TabIcon size={20} strokeWidth={2} aria-hidden="true" focusable="false" />
              <span className="w-full truncate px-1 text-center" style={{ fontSize: 12 }}>
                {tab.label}
              </span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}
