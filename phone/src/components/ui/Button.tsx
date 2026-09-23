import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { MouseEventHandler, ReactNode } from "react";
import { STROKE } from "./icons";

export type ButtonVariant = "primary" | "secondary" | "tertiary";

export interface ButtonProps {
  variant?: ButtonVariant;
  /** Renders a link (next/link) instead of a button. */
  href?: string;
  onClick?: MouseEventHandler<HTMLElement>;
  disabled?: boolean;
  type?: "button" | "submit" | "reset";
  children: ReactNode;
  /** Appended last. A same-property override needs Tailwind's `!` (e.g. `min-h-9!`). */
  className?: string;
  /** Required when the label is an icon alone. */
  ariaLabel?: string;
}

const BASE =
  "inline-flex select-none items-center justify-center gap-1 whitespace-nowrap rounded-full font-semibold transition-colors duration-120";

/** Pressed fills darken 8% (mixing 8% black in), 120 ms. */
const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "min-h-12 w-full bg-ink px-6 text-page text-[length:calc(1rem*var(--type-scale))] leading-[calc(1.25rem*var(--type-scale))] active:bg-[color-mix(in_srgb,var(--ink)_92%,black)]",
  secondary:
    "type-secondary min-h-10 bg-surface-2 px-4 text-text active:bg-[color-mix(in_srgb,var(--surface-2)_92%,black)]",
  tertiary: "type-secondary min-h-11 text-ink active:text-[color-mix(in_srgb,var(--ink)_92%,black)]",
};

const DISABLED = "disabled:bg-surface-2 disabled:text-muted";

/**
 * The app's one button, three weights. Primary is the single full-width pill
 * on a screen; secondary sits in bars and rows; tertiary is a text link with a
 * trailing arrow ("Get started →"). With `href` it is a link. With neither
 * `href`, `onClick` nor a submit type it renders as a plain span: the label of
 * a card that is itself the link.
 */
export function Button({
  variant = "primary",
  href,
  onClick,
  disabled = false,
  type = "button",
  children,
  className = "",
  ariaLabel,
}: ButtonProps) {
  const classes = `${BASE} ${VARIANT[variant]} ${className}`.trim();
  const content = (
    <>
      {children}
      {variant === "tertiary" ? <ArrowRight size={16} strokeWidth={STROKE} aria-hidden="true" /> : null}
    </>
  );

  if (href && !disabled) {
    return (
      <Link href={href} onClick={onClick} aria-label={ariaLabel} className={classes}>
        {content}
      </Link>
    );
  }
  if (onClick || disabled || type !== "button") {
    return (
      <button type={type} onClick={onClick} disabled={disabled} aria-label={ariaLabel} className={`${classes} ${DISABLED}`}>
        {content}
      </button>
    );
  }
  return <span className={classes}>{content}</span>;
}
