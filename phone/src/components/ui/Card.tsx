import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Chip, type ChipTone } from "./Chip";
import { PhotoPanel, type PhotoHeight, type Tint } from "./PhotoPanel";

export interface CardProps {
  tint: Tint;
  icon: LucideIcon;
  title: string;
  /** One secondary line under the title. */
  line?: string;
  /** One chip's content. */
  chip?: ReactNode;
  chipTone?: ChipTone;
  /** A tertiary Button ("Open →") or a primary Button. Inside a linked card, pass a Button with no handler. */
  action?: ReactNode;
  /** Makes the whole card the link. */
  href?: string;
  photoHeight?: PhotoHeight;
  children?: ReactNode;
}

const SURFACE = "block overflow-hidden rounded-card bg-surface text-text";
const PRESSED = "transition-colors duration-200 active:bg-[color-mix(in_srgb,var(--surface)_92%,black)]";

/** A card: photo panel on top, then title, one line, one chip and the action. */
export function Card({ tint, icon, title, line, chip, chipTone, action, href, photoHeight = 96, children }: CardProps) {
  const body = (
    <>
      {/* The panel runs edge to edge; the card's own corners clip it. */}
      <div className="*:rounded-none">
        <PhotoPanel tint={tint} icon={icon} height={photoHeight} />
      </div>
      <div className="p-card">
        <h2 className="type-card-title m-0 text-ink">{title}</h2>
        {line ? <p className="type-secondary m-0 mt-1 text-muted">{line}</p> : null}
        {chip ? (
          <div className="mt-3">
            <Chip tone={chipTone}>{chip}</Chip>
          </div>
        ) : null}
        {children}
        {action ? <div className="mt-4">{action}</div> : null}
      </div>
    </>
  );

  if (href) {
    return (
      <Link href={href} className={`${SURFACE} ${PRESSED}`}>
        {body}
      </Link>
    );
  }
  return <div className={SURFACE}>{body}</div>;
}
