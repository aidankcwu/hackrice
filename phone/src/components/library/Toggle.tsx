"use client";

export interface ToggleProps {
  on: boolean;
  onChange: (on: boolean) => void;
  /** The item the switch controls; its accessible name. */
  label: string;
  /** Lets a visible <label htmlFor> name the switch instead of `label`, so tapping the row text toggles too. */
  id?: string;
}

/**
 * The one switch of the system: 44 px tall, ink track and page knob when on,
 * surface-2 track and muted knob when off, 200 ms. The whole track is the
 * target. Shared by the protocol review, Concierge and Account.
 */
export function Toggle({ on, onChange, label, id }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={on}
      aria-label={id ? undefined : label}
      onClick={() => onChange(!on)}
      className={`motion relative h-11 w-[72px] shrink-0 rounded-full p-1 ${on ? "bg-ink" : "bg-surface-2"}`}
    >
      <span
        aria-hidden="true"
        className={`motion block size-9 rounded-full ${on ? "translate-x-7 bg-page" : "translate-x-0 bg-muted"}`}
      />
    </button>
  );
}
