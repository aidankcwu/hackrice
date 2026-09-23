"use client";

import { Button, Sheet } from "@/components/ui";
import { BASELINE_CARD } from "@/content/biomarkers";

export interface BaselineSheetProps {
  open: boolean;
  onClose: () => void;
}

/** What "Get your baseline" means: one blood draw for the first fifteen markers, the rest from a test or a device. */
export function BaselineSheet({ open, onClose }: BaselineSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} title={BASELINE_CARD.title} id="baseline">
      <p className="type-body m-0 mt-4 text-text">
        One blood draw covers the first fifteen, from ApoB to DunedinPACE: lipids, inflammation, glucose, liver and
        kidney, micronutrients, hormones and pace of aging.
      </p>
      <p className="type-body m-0 mt-3 text-text">
        Fitness comes from a test and recovery from your devices. When the results arrive, type each one in with
        Add result and the seeded value steps aside.
      </p>
      <p className="type-caption m-0 mt-3 text-muted">Targets are set by your clinician; the app never sets one.</p>
      <div className="mt-4">
        <Button variant="secondary" onClick={onClose} className="w-full">
          Done
        </Button>
      </div>
    </Sheet>
  );
}
