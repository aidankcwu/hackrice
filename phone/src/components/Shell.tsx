"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { CalendarDays, ChevronLeft, ListChecks, Settings, type LucideIcon } from "lucide-react";
import { SCREENS, TABS, type ScreenId } from "@/lib/screens";

/** One icon per meaning; Settings is the gear in the top bar. */
const ICONS: Record<ScreenId, LucideIcon> = {
  today: CalendarDays,
  protocol: ListChecks,
  settings: Settings,
};

/** Tab bar: a 54 px item row inside a 4 px glass rim, floating above the home indicator. */
const TAB_BAR_HEIGHT = 62;
const TAB_BAR_BOTTOM = "max(16px, env(safe-area-inset-bottom))";

export interface ShellProps {
  screen: ScreenId;
  /** Fixtures mode only: pins the appearance for screenshots. */
  theme?: "light" | "dark";
  /** Fixtures mode only: text size multiplier (2 = accessibility XXXL). */
  scale?: number;
  children?: ReactNode;
}

/**
 * The phone app's frame, native-first: a navigation bar with the Settings gear,
 * the large title, the screen's content, and the two-tab bar. Settings is a
 * pushed screen: a back button instead of the gear, and no tab bar.
 */
export function Shell({ screen, theme, scale, children }: ShellProps) {
  const { title } = SCREENS[screen];
  const pushed = !TABS.includes(screen);
  const headerRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const titleHidden = useScrolledUnder(titleRef, headerRef);

  return (
    <div
      data-theme={theme}
      style={scale ? ({ "--type-scale": scale } as CSSProperties) : undefined}
      className="mx-auto flex min-h-dvh w-full max-w-[430px] flex-col bg-page text-text"
    >
      <header ref={headerRef} className="sticky top-0 z-20 bg-page" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        <div className="relative flex h-11 items-center px-1">
          {pushed ? <BackButton /> : null}
          {/* The inline title appears once the large title has scrolled under the bar. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-14 truncate text-center text-[17px] font-semibold text-ink transition-opacity duration-200"
            style={{ opacity: titleHidden ? 1 : 0 }}
          >
            {title}
          </span>
          {pushed ? null : (
            <Link
              href={SCREENS.settings.href}
              aria-label={SCREENS.settings.title}
              className="ml-auto grid size-11 place-items-center rounded-full text-ink"
            >
              <Settings size={22} strokeWidth={2} aria-hidden="true" />
            </Link>
          )}
        </div>
      </header>

      <main
        className="flex-1 px-gutter"
        style={{ paddingBottom: pushed ? 32 : `calc(${TAB_BAR_HEIGHT}px + ${TAB_BAR_BOTTOM} + 24px)` }}
      >
        <h1 ref={titleRef} className="type-large-title m-0 pb-2 text-ink">
          {title}
        </h1>
        {children}
      </main>

      {pushed ? null : <TabBar active={screen} />}
    </div>
  );
}

function TabBar({ active }: { active: ScreenId }) {
  return (
    <nav
      aria-label="Tabs"
      className="pointer-events-none fixed inset-x-0 z-30 mx-auto flex max-w-[430px] justify-center px-4"
      style={{ bottom: TAB_BAR_BOTTOM }}
    >
      <div className="glass pointer-events-auto flex rounded-full p-1">
        {TABS.map((id) => {
          const on = id === active;
          const Icon = ICONS[id];
          return (
            <Link
              key={id}
              href={SCREENS[id].href}
              aria-current={on ? "page" : undefined}
              className={`flex h-[54px] w-24 flex-col items-center justify-center gap-1 rounded-full transition-colors duration-200 ${
                on ? "bg-[var(--glass-selected)] text-ink" : "text-muted"
              }`}
            >
              <Icon size={24} strokeWidth={on ? 2.25 : 2} aria-hidden="true" />
              <span className="text-[11px] leading-none font-semibold">{SCREENS[id].title}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

function BackButton() {
  const router = useRouter();
  return (
    <button
      type="button"
      aria-label="Back"
      onClick={() => (window.history.length > 1 ? router.back() : router.push(SCREENS.today.href))}
      className="grid size-11 place-items-center rounded-full text-ink"
    >
      <ChevronLeft size={28} strokeWidth={2} aria-hidden="true" />
    </button>
  );
}

/** True once `target` has scrolled up under the sticky `bar`. */
function useScrolledUnder(target: RefObject<HTMLElement | null>, bar: RefObject<HTMLElement | null>): boolean {
  const [under, setUnder] = useState(false);
  useEffect(() => {
    const element = target.current;
    if (!element) return;
    const barHeight = bar.current?.offsetHeight ?? 0;
    const observer = new IntersectionObserver(([entry]) => setUnder(!entry.isIntersecting), {
      rootMargin: `-${barHeight}px 0px 0px 0px`,
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [target, bar]);
  return under;
}
