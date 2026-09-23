"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Field, ICON_SIZES, InsetList, LoadingState, STROKE, SegmentedControl } from "@/components/ui";
import { PERMISSIONS, PERSONA_PLACEHOLDER, TALKATIVENESS, type PermissionId } from "@/content/concierge";
import type { ConciergeSettings, Talkativeness } from "@/content/types";
import { useConcierge } from "@/lib/conciergeStore";
import { PersonaSheet } from "./PersonaSheet";
import { SwitchRow } from "./SwitchRow";

const TALK_OPTIONS = TALKATIVENESS.map(({ id, label }) => ({ id, label }));

/**
 * Concierge, pushed from the menu: how the whisper behaves, as inset grouped
 * lists. Every control writes to the phone's own store as it changes; the
 * only button is "Save" inside the persona sheet.
 */
export function Concierge() {
  const { settings, loaded, update } = useConcierge();
  const [personaOpen, setPersonaOpen] = useState(false);
  const [personaDraft, setPersonaDraft] = useState("");

  if (!loaded) return <LoadingState line="Reading your settings…" blocks={4} />;

  const talk = TALKATIVENESS.find((option) => option.id === settings.talkativeness) ?? TALKATIVENESS[1];
  const setPermission = (id: PermissionId, on: boolean) => {
    const patch: Partial<ConciergeSettings> = {};
    patch[id] = on;
    update(patch);
  };
  const openPersona = () => {
    setPersonaDraft(settings.persona);
    setPersonaOpen(true);
  };
  const savePersona = (persona: string) => {
    update({ persona });
    setPersonaOpen(false);
  };

  return (
    <div className="mt-4 flex flex-col gap-section">
      <InsetList label="How talkative">
        <li>
          <div className="grid min-h-row items-center px-4 py-2">
            <SegmentedControl<Talkativeness>
              options={TALK_OPTIONS}
              value={settings.talkativeness}
              onChange={(talkativeness) => update({ talkativeness })}
              size={40}
              ariaLabel="How talkative"
            />
          </div>
        </li>
        <li className="relative before:absolute before:top-0 before:right-0 before:left-14 before:hairline">
          <p aria-live="polite" className="type-secondary m-0 px-4 py-3 text-muted">
            {talk.line}
          </p>
        </li>
      </InsetList>

      <InsetList label="Quiet hours">
        <li className="px-4 last:*:after:hidden [&>label]:has-focus-visible:-outline-offset-2">
          <Field
            id="quiet-start"
            label="Start"
            type="time"
            value={settings.quietStart}
            onChange={(quietStart) => {
              if (quietStart) update({ quietStart });
            }}
          />
        </li>
        <li className="px-4 last:*:after:hidden [&>label]:has-focus-visible:-outline-offset-2">
          <Field
            id="quiet-end"
            label="End"
            type="time"
            value={settings.quietEnd}
            onChange={(quietEnd) => {
              if (quietEnd) update({ quietEnd });
            }}
          />
        </li>
      </InsetList>

      <InsetList label="Voice">
        <SwitchRow
          id="voice-on-glasses"
          label="Voice on the glasses"
          line="When off, nudges arrive as notifications"
          checked={settings.voiceOnGlasses}
          onChange={(voiceOnGlasses) => update({ voiceOnGlasses })}
        />
      </InsetList>

      <InsetList label="On its own">
        {PERMISSIONS.map((permission) => (
          <SwitchRow
            key={permission.id}
            id={`permission-${permission.id}`}
            label={permission.label}
            line={permission.line}
            checked={settings[permission.id]}
            onChange={(on) => setPermission(permission.id, on)}
          />
        ))}
      </InsetList>

      <InsetList label="Persona">
        <li>
          <button
            type="button"
            onClick={openPersona}
            aria-haspopup="dialog"
            className="flex min-h-row w-full items-center gap-2 px-4 text-left transition-colors duration-120 focus-visible:-outline-offset-2 active:bg-surface-2"
          >
            <span className={`type-body line-clamp-2 min-w-0 flex-1 py-2 ${settings.persona ? "text-text" : "text-muted"}`}>
              {settings.persona ? (
                <>
                  <span className="sr-only">Persona: </span>
                  {settings.persona}
                </>
              ) : (
                PERSONA_PLACEHOLDER
              )}
            </span>
            <ChevronRight size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0 text-muted" aria-hidden="true" />
          </button>
        </li>
      </InsetList>

      <PersonaSheet
        open={personaOpen}
        value={personaDraft}
        onChange={setPersonaDraft}
        onSave={savePersona}
        onClose={() => setPersonaOpen(false)}
      />
    </div>
  );
}
