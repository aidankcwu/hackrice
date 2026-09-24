"use client";

import { Compass, LayoutGrid, UserRound, X, type LucideIcon } from "lucide-react";
import { useEffect, useRef, type MouseEvent } from "react";
import { Button, Card, ICON_SIZES, MEANING_ICONS, STROKE, type Tint } from "@/components/ui";
import { isEmbedded } from "@/lib/embed";
import { NATIVE_TABS, SCREENS, type ScreenId } from "@/lib/screens";

export interface MenuProps {
  onClose: () => void;
}

interface MenuItem {
  screen: ScreenId;
  /** The card's title; differs from the screen's own where the menu speaks in the first person. */
  title: string;
  tint: Tint;
  icon: LucideIcon;
  line: string;
  chip: string;
  /** Only when embedded: stands in for the top pill's button, which embed mode hides. */
  embedOnly?: boolean;
}

/** The menu's cards, in order. Find, Library and Account use icons outside MEANING_ICONS so no meaning is shared. */
const ITEMS: readonly MenuItem[] = [
  { screen: "find", title: "Find my protocol", tint: "sage", icon: Compass, line: "Six questions, one template", chip: "6 questions", embedOnly: true },
  { screen: "protocol", title: "My protocol", tint: "sage", icon: MEANING_ICONS.protocol, line: "Today's windows and doses", chip: "Today" },
  { screen: "library", title: "Protocol library", tint: "sand", icon: LayoutGrid, line: "Blueprint, Sleep first, New parent, and more", chip: "6 templates" },
  { screen: "treatments", title: "Treatments", tint: "slate", icon: MEANING_ICONS.pill, line: "Peptides, GLP-1s, supplements", chip: "24 items" },
  { screen: "tests", title: "Tests", tint: "bluegrey", icon: MEANING_ICONS.test, line: "PVT-B, 2-back, DSST, Stroop", chip: "3 minutes" },
  { screen: "biomarkers", title: "Biomarkers", tint: "stone", icon: MEANING_ICONS.biomarker, line: "Your panel and pace of aging", chip: "Get your baseline" },
  { screen: "devices", title: "Devices", tint: "slate", icon: MEANING_ICONS.glasses, line: "Glasses, ring, watch, air", chip: "Connect" },
  { screen: "concierge", title: "Concierge", tint: "sage", icon: MEANING_ICONS.concierge, line: "How the whisper behaves", chip: "Settings" },
  { screen: "sources", title: "Sources", tint: "sand", icon: MEANING_ICONS.sources, line: "Every number, with its paper", chip: "Grade A to C" },
  { screen: "claims", title: "What we don’t claim", tint: "stone", icon: MEANING_ICONS.mind, line: "Nine things the app will not say", chip: "Honest" },
  { screen: "settings", title: "Account", tint: "bluegrey", icon: UserRound, line: "Backend, token, appearance", chip: "Settings" },
];

/**
 * The cards to show. Embedded in the native app, the native tabs (Today,
 * Protocol) are not offered, and Find takes the place of the hidden top pill.
 */
export function menuItems(embedded: boolean): readonly MenuItem[] {
  return ITEMS.filter((item) => (embedded ? !NATIVE_TABS.includes(item.screen) : !item.embedOnly));
}

/**
 * The menu: a full-screen overlay on the page colour, opened from the top
 * bar's hamburger, fading in over 200 ms. One card per destination, two
 * columns from 360 px wide. Body scroll is locked while it is open; Escape,
 * the close button, or a tap on any card closes it.
 */
export function Menu({ onClose }: MenuProps) {
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButton.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // A card is a link; closing here covers the route that would not remount the shell.
  const onCardClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target instanceof Element && event.target.closest("a")) onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="menu-title"
      className="fade-in fixed inset-0 z-40 overflow-y-auto bg-page text-text"
    >
      <div
        className="mx-auto w-full max-w-[430px] px-gutter"
        style={{ paddingTop: "env(safe-area-inset-top)", paddingBottom: "max(24px, env(safe-area-inset-bottom))" }}
      >
        <div className="flex min-h-13 items-center justify-between pt-2">
          <h2 id="menu-title" className="type-screen-title m-0 text-ink">
            Menu
          </h2>
          <button
            ref={closeButton}
            type="button"
            aria-label="Close menu"
            onClick={onClose}
            className="-mr-1 grid size-11 shrink-0 place-items-center rounded-full text-ink transition-colors duration-120 active:bg-surface-2"
          >
            <X size={ICON_SIZES.card} strokeWidth={STROKE} aria-hidden="true" />
          </button>
        </div>

        <div onClick={onCardClick} className="mt-section grid grid-cols-1 gap-3 min-[360px]:grid-cols-2">
          {menuItems(isEmbedded()).map((item) => (
            <Card
              key={item.screen}
              href={SCREENS[item.screen].href}
              tint={item.tint}
              icon={item.icon}
              title={item.title}
              line={item.line}
              chip={item.chip}
              action={<Button variant="tertiary">Open</Button>}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
