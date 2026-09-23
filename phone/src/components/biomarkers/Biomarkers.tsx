"use client";

import { useState } from "react";
import { Button, Card, InsetList, MEANING_ICONS } from "@/components/ui";
import { BASELINE_CARD, BIOMARKER_GROUPS, BIOMARKERS, type BiomarkerCard } from "@/content/biomarkers";
import { readingFor, useBiomarkerResults } from "@/lib/biomarkerStore";
import { AddResultSheet } from "./AddResultSheet";
import { BaselineSheet } from "./BaselineSheet";
import { BiomarkerRow } from "./BiomarkerRow";

/** The panel's groups in content order, each with its markers; a group with none is left out. */
const GROUPS: readonly { group: string; markers: BiomarkerCard[] }[] = BIOMARKER_GROUPS.map((group) => ({
  group,
  markers: BIOMARKERS.filter((marker) => marker.group === group),
})).filter(({ markers }) => markers.length > 0);

/**
 * The Biomarkers screen: the "Get your baseline" card, then one inset list per
 * group. A typed-in result replaces the seeded value on its row; the results
 * live in `localStorage` (`brian.biomarkers`).
 */
export function Biomarkers() {
  const { results, add } = useBiomarkerResults();
  const [baselineOpen, setBaselineOpen] = useState(false);
  const [adding, setAdding] = useState<BiomarkerCard | null>(null);

  return (
    <div className="mt-2 flex flex-col gap-section">
      <Card
        tint="stone"
        icon={MEANING_ICONS.biomarker}
        photoHeight={120}
        title={BASELINE_CARD.title}
        line={BASELINE_CARD.line}
        chip={`${BIOMARKERS.length} markers`}
        action={<Button onClick={() => setBaselineOpen(true)}>{BASELINE_CARD.title}</Button>}
      />

      {GROUPS.map(({ group, markers }) => (
        <InsetList key={group} label={group}>
          {markers.map((marker) => (
            <BiomarkerRow
              key={marker.id}
              biomarker={marker}
              reading={readingFor(marker, results)}
              onAdd={() => setAdding(marker)}
            />
          ))}
        </InsetList>
      ))}

      <BaselineSheet open={baselineOpen} onClose={() => setBaselineOpen(false)} />
      <AddResultSheet
        biomarker={adding}
        onClose={() => setAdding(null)}
        onSave={(result) => {
          add(result);
          setAdding(null);
        }}
      />
    </div>
  );
}
