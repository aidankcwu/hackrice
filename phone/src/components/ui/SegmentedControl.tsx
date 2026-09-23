export interface SegmentOption<T extends string> {
  id: T;
  label: string;
}

export interface SegmentedControlProps<T extends string> {
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (id: T) => void;
  /** Track height. Targets stay 44 px through an invisible extension. */
  size?: 32 | 40;
  ariaLabel: string;
}

/** A pill track with equal segments; the chosen one is ink on page. */
export function SegmentedControl<T extends string>({ options, value, onChange, size = 32, ariaLabel }: SegmentedControlProps<T>) {
  const tall = size === 40;
  return (
    <div role="group" aria-label={ariaLabel} className={`inline-grid grid-flow-col auto-cols-fr rounded-full bg-surface-2 p-0.5 ${tall ? "h-10" : "h-8"}`}>
      {options.map((option) => {
        const on = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(option.id)}
            className={`relative rounded-full px-4 font-semibold whitespace-nowrap transition-colors duration-200 before:absolute before:inset-x-0 before:-inset-y-2 ${
              tall ? "type-secondary" : "type-caption"
            } ${on ? "bg-ink text-page" : "text-text"}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
