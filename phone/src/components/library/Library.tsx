"use client";

import { Button, Card, Chip, MEANING_ICONS } from "@/components/ui";
import { PROTOCOLS } from "@/content/protocols";
import { useMyProtocol } from "@/lib/myProtocol";
import { flagChip, itemCount } from "./format";

export interface LibraryProps {
  /** Fixtures mode: the screenshot params to carry onto a pushed detail. */
  query?: string;
}

/**
 * The protocol library: one card per seeded template, the menu's anatomy at
 * 120 px, the template's chip plus its size and its one notable rule, and
 * "See the day" onto the template's detail. The template already in My
 * protocol says so.
 */
export function Library({ query = "" }: LibraryProps) {
  const { protocol, loaded } = useMyProtocol();

  return (
    <>
      <p className="type-secondary m-0 mt-1 text-muted">Six seeded templates. Every item is a toggle before it lands in My protocol.</p>
      <div className="mt-4 flex flex-col gap-4">
        {PROTOCOLS.map((template) => {
          const inUse = loaded && protocol.templateId === template.id;
          const flag = flagChip(template.flags);
          return (
            <Card
              key={template.id}
              href={`/library/${template.id}${query}`}
              tint={template.tint}
              icon={MEANING_ICONS[template.icon]}
              title={template.name}
              line={template.line}
              photoHeight={120}
              action={<Button variant="tertiary">See the day</Button>}
            >
              <div className="mt-3 flex flex-wrap gap-2">
                <Chip>{template.chip}</Chip>
                <Chip>{itemCount(template.items.length)}</Chip>
                {flag ? <Chip>{flag}</Chip> : null}
                {inUse ? <Chip tone="good">In My protocol</Chip> : null}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
