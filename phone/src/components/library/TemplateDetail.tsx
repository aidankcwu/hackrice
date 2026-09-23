"use client";

import { Button, Chip, InsetList, MEANING_ICONS, PhotoPanel, STROKE } from "@/components/ui";
import type { ProtocolTemplate } from "@/content/types";
import { useMyProtocol } from "@/lib/myProtocol";
import { ItemRow } from "./ItemRow";
import { SECTIONS, flagLines, itemCount } from "./format";

export interface TemplateDetailProps {
  template: ProtocolTemplate;
  /** Fixtures mode: the screenshot params to carry onto the review. */
  query?: string;
}

/**
 * One template's day: a 200 px hero, the name at 34, its chips, its rules one
 * line each, then every item in inset lists by section (Sleep · Light ·
 * Movement · Meals · Doses · Screens · Sauna · Supplements · Hydration, only
 * the sections it has). "Use this protocol" opens the guided review.
 */
export function TemplateDetail({ template, query = "" }: TemplateDetailProps) {
  const { protocol, loaded } = useMyProtocol();
  const inUse = loaded && protocol.templateId === template.id;
  const rules = [...(template.notes ?? []), ...flagLines(template.flags)];
  const Check = MEANING_ICONS.check;

  return (
    <>
      <div className="pt-2">
        <PhotoPanel tint={template.tint} icon={MEANING_ICONS[template.icon]} height={200} />
      </div>
      <h2 className="type-screen-title m-0 mt-4 text-ink">{template.name}</h2>
      <p className="type-secondary m-0 mt-1 text-muted">{template.line}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Chip>{template.chip}</Chip>
        <Chip>{itemCount(template.items.length)}</Chip>
        {template.chips.map((chip) => (
          <Chip key={chip}>{chip}</Chip>
        ))}
        {inUse ? <Chip tone="good">In My protocol</Chip> : null}
      </div>

      {rules.length > 0 ? (
        <ul aria-label="Rules" className="m-0 mt-4 flex list-none flex-col gap-2 p-0">
          {rules.map((rule) => (
            <li key={rule} className="type-body flex items-start gap-2 text-text">
              <Check size={18} strokeWidth={STROKE} className="mt-[3px] shrink-0 text-muted" aria-hidden="true" />
              <span>{rule}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {SECTIONS.map(({ kind, label }) => {
        const items = template.items.filter((item) => item.kind === kind);
        if (items.length === 0) return null;
        return (
          <div key={kind} className="mt-section">
            <InsetList label={label}>
              {items.map((item) => (
                <ItemRow key={item.id} item={item} />
              ))}
            </InsetList>
          </div>
        );
      })}

      <div className="sticky bottom-0 -mx-gutter mt-section bg-page px-gutter pt-3" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        <Button href={`/library/${template.id}/review${query}`}>Use this protocol</Button>
      </div>
    </>
  );
}
