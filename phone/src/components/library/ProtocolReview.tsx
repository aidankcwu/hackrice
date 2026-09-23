"use client";

import { useState } from "react";
import { Button, InsetList, MEANING_ICONS, STROKE } from "@/components/ui";
import type { ProtocolTemplate } from "@/content/types";
import { adoptTemplate, type MyProtocol } from "@/lib/myProtocol";
import { ItemRow } from "./ItemRow";
import { Toggle } from "./Toggle";
import { flagLines } from "./format";

export interface ProtocolReviewProps {
  template: ProtocolTemplate;
  source: "template" | "find";
  /** Called with the saved protocol after "Save to My protocol". */
  onDone: (protocol: MyProtocol) => void;
}

/**
 * The guided review: every item of the template as a row with a 44 px switch,
 * all on to start; the template's flags one line each; "Turn all on" or "Turn
 * all off"; and "Save to My protocol" pinned at the bottom, which replaces My
 * protocol with the items left on. Find my protocol reuses it as its last step.
 */
export function ProtocolReview({ template, source, onDone }: ProtocolReviewProps) {
  const [off, setOff] = useState<ReadonlySet<string>>(() => new Set());
  const total = template.items.length;
  const enabled = template.items.filter((item) => !off.has(item.id));
  const allOn = off.size === 0;
  const flags = flagLines(template.flags);
  const Check = MEANING_ICONS.check;

  const setItem = (id: string, on: boolean) => {
    setOff((previous) => {
      const next = new Set(previous);
      if (on) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const setAll = (on: boolean) => setOff(on ? new Set() : new Set(template.items.map((item) => item.id)));

  const save = () => {
    onDone(
      adoptTemplate(
        template,
        enabled.map((item) => item.id),
        source,
      ),
    );
  };

  return (
    <>
      <p className="type-secondary m-0 mt-1 text-muted">Everything you leave on lands in My protocol. Turn off what you will not do.</p>

      {flags.length > 0 ? (
        <ul aria-label="How this protocol scores" className="m-0 mt-4 flex list-none flex-col gap-2 p-0">
          {flags.map((flag) => (
            <li key={flag} className="type-body flex items-start gap-2 text-text">
              <Check size={18} strokeWidth={STROKE} className="mt-[3px] shrink-0 text-muted" aria-hidden="true" />
              <span>{flag}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex min-h-11 items-center justify-between gap-2">
        <p className="type-secondary m-0 text-muted tabular-nums" aria-live="polite">
          {enabled.length} of {total} on
        </p>
        <Button variant="tertiary" onClick={() => setAll(!allOn)}>
          {allOn ? "Turn all off" : "Turn all on"}
        </Button>
      </div>

      <div className="mt-2">
        <InsetList>
          {template.items.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              trailing={<Toggle on={!off.has(item.id)} onChange={(on) => setItem(item.id, on)} label={item.name} />}
            />
          ))}
        </InsetList>
      </div>

      <div className="sticky bottom-0 -mx-gutter mt-section bg-page px-gutter pt-3" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        <Button onClick={save} disabled={enabled.length === 0}>
          Save to My protocol
        </Button>
      </div>
    </>
  );
}
