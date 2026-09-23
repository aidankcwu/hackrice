"use client";

import { ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Menu } from "@/components/menu/Menu";
import { Button, STROKE, TabBar, TopBar } from "@/components/ui";
import { SCREENS, TABS, type ScreenId } from "@/lib/screens";

/**
 * A tab's content ends this far above the bottom edge: the tab bar (64), its
 * 8 px float, and 16 px clear, plus the home indicator, so the last row scrolls
 * out from under the bar.
 */
const TAB_CLEARANCE = "calc(88px + env(safe-area-inset-bottom))";

export interface ShellProps {
  screen: ScreenId;
  /** A detail pushed from `screen` (e.g. a ledger row): its own title, a back button, no tab bar. */
  pushed?: boolean;
  /** Overrides the screen's title, for a pushed detail. */
  title?: string;
  /** Fixtures mode only: pins the appearance for screenshots. */
  theme?: "light" | "dark";
  /** Fixtures mode only: text size multiplier (2 = accessibility XXXL). */
  scale?: number;
  /** A screen's own toolbar button (Protocol's "+"): beside the screen title on a tab, at the right of the bar on a pushed screen. */
  action?: ReactNode;
  children?: ReactNode;
}

/**
 * The phone app's frame: a floating top pill (hamburger, wordmark, and on the
 * four tabs the "Find my protocol" pill), the screen title once at the top of
 * the content, the content, and the floating tab bar. Every other screen is
 * pushed: the pill carries a back button and the title instead, and there is
 * no tab bar. The hamburger opens the Menu over everything.
 */
export function Shell({ screen, pushed: pushedDetail, title: titleOverride, theme, scale, action, children }: ShellProps) {
  const title = titleOverride ?? SCREENS[screen].title;
  const pushed = pushedDetail || !TABS.includes(screen);
  const [menuOpen, setMenuOpen] = useState(false);
  /** What had focus when the menu opened (the hamburger); focus returns there on close. */
  const opener = useRef<HTMLElement | null>(null);

  const openMenu = () => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setMenuOpen(true);
  };
  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // Focus returns to the opener only after the close has committed: the page
  // chrome is inert while the menu is open, and an inert element refuses focus.
  useEffect(() => {
    if (menuOpen) return;
    const target = opener.current;
    opener.current = null;
    target?.focus();
  }, [menuOpen]);

  return (
    <div
      data-theme={theme}
      style={scale ? ({ "--type-scale": scale } as CSSProperties) : undefined}
      className="mx-auto flex min-h-dvh w-full max-w-[430px] flex-col bg-page text-text"
    >
      {/* Everything under the menu is inert while it is open, so focus and screen readers stay inside the overlay. */}
      <div className="contents" inert={menuOpen || undefined}>
        <header className="sticky top-0 z-20" style={{ paddingTop: "env(safe-area-inset-top)" }}>
          {pushed ? (
            <PushedBar screen={screen} title={title} action={action} />
          ) : (
            <TopBar
              onMenu={openMenu}
              mainTab
              action={
                <Button variant="secondary" href={SCREENS.find.href} className="min-h-9! px-3!">
                  {SCREENS.find.title}
                </Button>
              }
            />
          )}
        </header>

        <main className="flex-1 px-gutter" style={{ paddingBottom: pushed ? 32 : TAB_CLEARANCE }}>
          {pushed ? null : (
            <div className="flex items-center justify-between gap-2 pt-4 pb-2">
              <h1 className="type-screen-title m-0 text-ink">{title}</h1>
              {action}
            </div>
          )}
          {children}
        </main>

        {pushed ? null : <TabBar active={screen} />}
      </div>
      {menuOpen ? <Menu onClose={closeMenu} /> : null}
    </div>
  );
}

/** The top pill on a pushed screen: the back button where the hamburger was, the title where the wordmark was. */
function PushedBar({ screen, title, action }: { screen: ScreenId; title: string; action?: ReactNode }) {
  return (
    <div className="px-2 pt-2">
      <div className="glass relative flex h-13 items-center rounded-full px-1">
        <BackButton screen={screen} />
        <h1 className="type-card-title pointer-events-none absolute inset-x-14 m-0 truncate text-center text-ink">{title}</h1>
        {action ? <div className="ml-auto flex items-center pr-1">{action}</div> : null}
      </div>
    </div>
  );
}

/** Back to where the detail was pushed from; with no history, to the tab it belongs to (Today for a menu screen). */
function BackButton({ screen }: { screen: ScreenId }) {
  const router = useRouter();
  const home = TABS.includes(screen) ? SCREENS[screen].href : SCREENS.today.href;
  return (
    <button
      type="button"
      aria-label="Back"
      onClick={() => (window.history.length > 1 ? router.back() : router.push(home))}
      className="grid size-11 shrink-0 place-items-center rounded-full text-ink transition-colors duration-120 active:bg-surface-2"
    >
      <ChevronLeft size={24} strokeWidth={STROKE} aria-hidden="true" />
    </button>
  );
}
