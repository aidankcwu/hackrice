"use client";

import { ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { Menu } from "@/components/menu/Menu";
import { Button, STROKE, TabBar, TopBar } from "@/components/ui";
import { isEmbedded } from "@/lib/embed";
import { backFallback, SCREENS, TABS, type ScreenId } from "@/lib/screens";

/**
 * A tab's content ends this far above the bottom edge: the tab bar (64), its
 * 8 px float, and 16 px clear, plus the home indicator, so the last row scrolls
 * out from under the bar. Embedded there is no web tab bar (the native one sits
 * outside the web view), so a tab ends like a pushed screen.
 */
const TAB_CLEARANCE = "pb-[calc(88px+env(safe-area-inset-bottom))] embed:pb-8";

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
 *
 * Embedded in the native app (`?embed=1`, src/lib/embed.ts) a tab drops the
 * top pill (and its Find pill), the hamburger, its own title and the tab bar:
 * the native inline title is the only title, the content starts where that
 * title row used to, and the Menu cannot be opened. A pushed screen keeps its
 * back bar.
 */
export function Shell(props: ShellProps) {
  return <ShellFrame {...props} embedded={useEmbedded()} />;
}

const noSubscribe = () => () => {};

/**
 * `isEmbedded()` after hydration, false on the server and during hydration.
 * Before hydration the `embed:` CSS variant hides the same chrome, so the
 * first paint already matches.
 */
function useEmbedded(): boolean {
  return useSyncExternalStore(noSubscribe, isEmbedded, () => false);
}

/** The Shell with embed mode decided by the caller (tests render both). */
export function ShellFrame({
  screen,
  pushed: pushedDetail,
  title: titleOverride,
  theme,
  scale,
  action,
  embedded,
  children,
}: ShellProps & { embedded: boolean }) {
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
        {pushed || !embedded ? (
          <header
            className={`sticky top-0 z-20 ${pushed ? "" : "embed:hidden"}`}
            style={{ paddingTop: "env(safe-area-inset-top)" }}
          >
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
        ) : null}

        <main
          className={`flex-1 px-gutter ${pushed ? "pb-8" : `${TAB_CLEARANCE} embed:pt-[calc(env(safe-area-inset-top)+16px)]`}`}
        >
          {pushed ? null : embedded ? (
            action ? <div className="flex justify-end pb-2">{action}</div> : null
          ) : (
            <div className="flex items-center justify-between gap-2 pt-4 pb-2 embed:hidden">
              <h1 className="type-screen-title m-0 text-ink">{title}</h1>
              {action}
            </div>
          )}
          {children}
        </main>

        {pushed || embedded ? null : (
          <div className="embed:hidden">
            <TabBar active={screen} />
          </div>
        )}
      </div>
      {menuOpen && !embedded ? <Menu onClose={closeMenu} /> : null}
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

/** Back to where the detail was pushed from; with no history, to the tab it belongs to (`backFallback`). */
function BackButton({ screen }: { screen: ScreenId }) {
  const router = useRouter();
  return (
    <button
      type="button"
      aria-label="Back"
      onClick={() => (window.history.length > 1 ? router.back() : router.push(backFallback(screen, isEmbedded())))}
      className="grid size-11 shrink-0 place-items-center rounded-full text-ink transition-colors duration-120 active:bg-surface-2"
    >
      <ChevronLeft size={24} strokeWidth={STROKE} aria-hidden="true" />
    </button>
  );
}
