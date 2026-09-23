"use client";

import { useState } from "react";
import { Button, Chip, InsetList, ListRow, MEANING_ICONS, PhotoPanel } from "@/components/ui";
import { CATEGORY_CARD_CHIP, CLINICIAN_DOSE_LINE, STATUS_LABEL } from "@/content/treatments";
import type { Treatment } from "@/content/types";
import { addItem, useMyProtocol } from "@/lib/myProtocol";

export interface TreatmentDetailProps {
  treatment: Treatment;
  /** Fixtures mode only: the screenshot params to carry onto links. */
  query?: string;
}

/** Content lines start lowercase so they can sit mid-sentence; a row starts a sentence. */
const sentence = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * One treatment, pushed from Treatments: a 200 px hero, the name at 34, the
 * category and status chips, then Timing · Route · What the glasses see ·
 * Status in inset lists. "Add to protocol" appends the treatment's item to My
 * protocol on this phone; once it is there the bar reads "Added" with a link
 * to My protocol. A drug's status says the dose is set by your clinician;
 * an amount never appears.
 */
export function TreatmentDetail({ treatment, query = "" }: TreatmentDetailProps) {
  const { protocol, loaded } = useMyProtocol();
  const [justAdded, setJustAdded] = useState(false);
  const inProtocol = loaded && protocol.items.some((item) => item.id === treatment.item.id && item.enabled);
  const added = justAdded || inProtocol;
  const status = STATUS_LABEL[treatment.status];
  const Check = MEANING_ICONS.check;

  const add = () => {
    addItem(treatment.item, "treatment");
    setJustAdded(true);
  };

  return (
    <>
      <div className="pt-2">
        <PhotoPanel tint={treatment.tint} icon={MEANING_ICONS[treatment.icon]} height={200} />
      </div>
      <h2 className="type-screen-title m-0 mt-4 text-ink">{treatment.name}</h2>
      <div className="mt-3 flex flex-wrap gap-2">
        <Chip>{CATEGORY_CARD_CHIP[treatment.categories[0]]}</Chip>
        <Chip>{status}</Chip>
      </div>

      <div className="mt-section flex flex-col gap-section">
        <InsetList label="Timing">
          <ListRow title={treatment.timing} />
        </InsetList>

        <InsetList label="Route">
          <ListRow title={treatment.route} />
        </InsetList>

        <InsetList label="What the glasses see">
          <ListRow icon={MEANING_ICONS.glasses} title={sentence(treatment.cameraLine)} />
        </InsetList>

        <InsetList label="Status">
          <ListRow title={status} detail={treatment.item.clinicianDose ? CLINICIAN_DOSE_LINE : undefined} />
        </InsetList>
      </div>

      {/* The one action, pinned above the home indicator while the sections scroll under it. */}
      <div
        aria-live="polite"
        className="sticky bottom-0 -mx-gutter mt-section bg-page px-gutter pt-3"
        style={{ paddingBottom: "calc(8px + env(safe-area-inset-bottom))" }}
      >
        {added ? (
          <div className="flex min-h-12 items-center justify-between gap-2">
            <Chip tone="good" icon={Check}>
              Added
            </Chip>
            <Button variant="tertiary" href={`/protocol${query}`}>
              See My protocol
            </Button>
          </div>
        ) : (
          <Button onClick={add}>Add to protocol</Button>
        )}
      </div>
    </>
  );
}
