"use client";

import { Button, Sheet } from "@/components/ui";
import { PERSONA_PLACEHOLDER } from "@/content/concierge";

export interface PersonaSheetProps {
  open: boolean;
  /** The draft, owned by the screen so each opening can start from the saved line. */
  value: string;
  onChange: (value: string) => void;
  /** Called with the trimmed draft; the screen saves and closes. */
  onSave: (persona: string) => void;
  onClose: () => void;
}

/**
 * The persona in the wearer's words: a bottom sheet with one textarea and the
 * screen's primary "Save". Closing any other way keeps the saved line.
 */
export function PersonaSheet({ open, value, onChange, onSave, onClose }: PersonaSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} title="Persona" id="persona">
      <label htmlFor="persona-text" className="sr-only">
        Persona, in your words
      </label>
      <textarea
        id="persona-text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={PERSONA_PLACEHOLDER}
        rows={4}
        autoComplete="off"
        enterKeyHint="done"
        className="type-body mt-4 block w-full resize-none rounded-tile bg-surface-2 p-4 text-text placeholder:text-muted"
      />
      <div className="mt-4">
        <Button onClick={() => onSave(value.trim())}>Save</Button>
      </div>
    </Sheet>
  );
}
