export interface FieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** "time" opens the system time picker, the value reads as a trailing time. */
  type?: "text" | "time";
  placeholder?: string;
  id: string;
}

/** A 56 px settings-style row: label left, value right, a hairline under. */
export function Field({ label, value, onChange, type = "text", placeholder, id }: FieldProps) {
  return (
    <label
      htmlFor={id}
      className="relative flex min-h-row items-center gap-4 rounded-sm after:absolute after:inset-x-0 after:bottom-0 after:hairline has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ink"
    >
      <span className="type-body shrink-0 text-text">{label}</span>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        enterKeyHint={type === "text" ? "done" : undefined}
        className="type-body min-w-0 flex-1 bg-transparent text-right text-text tabular-nums placeholder:text-muted focus:outline-none"
      />
    </label>
  );
}
