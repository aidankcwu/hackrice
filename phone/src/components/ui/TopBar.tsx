import { Menu } from "lucide-react";
import type { ReactNode } from "react";
import { ICON_SIZES, STROKE } from "./icons";

export interface TopBarProps {
  onMenu: () => void;
  /** Main tabs only: the pill Button on the right ("Find my protocol"), sized to 36 px here. */
  action?: ReactNode;
  mainTab?: boolean;
}

/** The floating top pill: hamburger, the wordmark, and on main tabs the action. Sticky positioning is the Shell's. */
export function TopBar({ onMenu, action, mainTab = false }: TopBarProps) {
  return (
    <div className="px-2 pt-2">
      <div className="glass relative flex h-13 items-center rounded-full px-1">
        <button
          type="button"
          aria-label="Menu"
          onClick={onMenu}
          className="grid size-11 shrink-0 place-items-center rounded-full text-ink transition-colors duration-120 active:bg-surface-2"
        >
          <Menu size={ICON_SIZES.card} strokeWidth={STROKE} aria-hidden="true" />
        </button>
        <span className="type-wordmark pointer-events-none absolute inset-x-14 text-center text-ink">Bryan</span>
        {mainTab && action ? <div className="ml-auto flex items-center pr-1 *:h-9 *:min-h-9">{action}</div> : null}
      </div>
    </div>
  );
}
