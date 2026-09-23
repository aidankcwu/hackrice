import Link from "next/link";
import { ChevronRight, type LucideIcon } from "lucide-react";
import type { MouseEventHandler, ReactNode } from "react";
import { ICON_SIZES, STROKE } from "./icons";

export interface InsetListProps {
  children: ReactNode;
  /** A short label above the group. */
  label?: string;
}

/** An inset grouped list: one surface, 20 px corners, rows split by hairlines inset 56 px. */
export function InsetList({ children, label }: InsetListProps) {
  return (
    <div>
      {label ? <p className="type-secondary m-0 mb-2 px-4 text-muted">{label}</p> : null}
      <ul aria-label={label} className="m-0 list-none overflow-hidden rounded-card bg-surface p-0">
        {children}
      </ul>
    </div>
  );
}

export interface ListRowProps {
  icon?: LucideIcon;
  title: string;
  /** A second line under the title. */
  detail?: string;
  /** A chip or a value at the right, before the chevron. */
  trailing?: ReactNode;
  href?: string;
  onClick?: MouseEventHandler<HTMLElement>;
  /** Defaults to on for a linked row. */
  chevron?: boolean;
  destructive?: boolean;
}

// The list clips its corners, so the focus ring is drawn inside the row.
const ROW = "flex min-h-row w-full items-center gap-2 pr-4 text-left focus-visible:-outline-offset-2";
const PRESSED = "transition-colors duration-120 active:bg-surface-2";
const HAIRLINE =
  "relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline";

/** One row of an InsetList: a link, a button, or plain. */
export function ListRow({ icon: Icon, title, detail, trailing, href, onClick, chevron = href !== undefined, destructive = false }: ListRowProps) {
  const inner = (
    <>
      {Icon ? (
        <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full bg-muted/20 text-muted">
          <Icon size={ICON_SIZES.list} strokeWidth={STROKE} />
        </span>
      ) : null}
      <span className="flex min-w-0 flex-1 flex-col py-2">
        <span className={`type-body ${destructive ? "text-bad" : "text-text"}`}>{title}</span>
        {detail ? <span className="type-secondary text-muted">{detail}</span> : null}
      </span>
      {trailing}
      {chevron ? <ChevronRight size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0 text-muted" aria-hidden="true" /> : null}
    </>
  );
  const plain = `${ROW} ${Icon ? "pl-3" : "pl-4"}`;
  const pressable = `${plain} ${PRESSED}`;

  return (
    <li className={HAIRLINE}>
      {href ? (
        <Link href={href} onClick={onClick} className={pressable}>
          {inner}
        </Link>
      ) : onClick ? (
        <button type="button" onClick={onClick} className={pressable}>
          {inner}
        </button>
      ) : (
        <div className={plain}>{inner}</div>
      )}
    </li>
  );
}
