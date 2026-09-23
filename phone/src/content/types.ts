// Shared content types. Every content module imports from "./types".
// Plain data types only: no React, no component imports, no icon libraries.

export type Tint = "stone" | "sage" | "sand" | "slate" | "bluegrey";

// The app maps these names to lucide icons; content never imports lucide.
export type IconName =
  | "caffeine"
  | "food"
  | "movement"
  | "light"
  | "sleep"
  | "screens"
  | "peptide"
  | "pill"
  | "alcohol"
  | "nap"
  | "sauna"
  | "water"
  | "people"
  | "air"
  | "mind"
  | "body"
  | "glasses"
  | "watch"
  | "test"
  | "biomarker"
  | "protocol"
  | "concierge"
  | "sources";

export type ItemKind =
  | "dose"
  | "meal"
  | "move"
  | "light"
  | "sleep"
  | "screens"
  | "sauna"
  | "supplement"
  | "hydration";

// What the glasses verify when an item is logged.
export type Verify =
  | "pen"
  | "vial_syringe"
  | "pill_bottle"
  | "blister"
  | "powder_tub"
  | "tube"
  | "topical"
  | "none";

// 0 = Monday.
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;

// Local "HH:MM". Anchored windows use Anchored instead.
export interface Window {
  start: string;
  end: string;
}

// A window measured from actual wake or bed time rather than the clock.
export interface Anchored {
  anchor: "wake" | "bed";
  offsetMin: number;
  lengthMin: number;
}

export interface ProtocolItemTemplate {
  id: string;
  name: string;
  kind: ItemKind;
  window: Window | Anchored;
  days: Weekday[];
  cycle?: { onDays: number; offDays: number } | { weekly: Weekday };
  verify: Verify;
  // When true the UI shows "dose set by your clinician". Never a dose amount.
  clinicianDose: boolean;
  note?: string;
}

export interface ProtocolTemplate {
  id: string;
  name: string;
  chip: string;
  tint: Tint;
  icon: IconName;
  line: string;
  chips: string[];
  // Short rules that apply to the whole template, e.g. "No caffeine".
  notes?: string[];
  items: ProtocolItemTemplate[];
  flags: {
    nightWakingsNeverRed: boolean;
    napsAnyTimeUnder20: boolean;
    ceilingsLabel: string | null;
    anchorToActualSleep: boolean;
  };
}

export type TreatmentStatus =
  | "approved"
  | "approved off-label"
  | "compounded"
  | "research-grade"
  | "supplement";

export type TreatmentCategory =
  | "longevity"
  | "weight"
  | "recovery"
  | "sleep"
  | "hormones"
  | "supplements";

export interface Treatment {
  id: string;
  name: string;
  categories: TreatmentCategory[];
  timing: string;
  route: string;
  cameraSees: Verify;
  cameraLine: string;
  status: TreatmentStatus;
  tint: Tint;
  icon: IconName;
  // What "Add to protocol" adds. clinicianDose true for drugs, false for supplements.
  item: ProtocolItemTemplate;
}

export interface TestDef {
  id: "pvt" | "nback" | "dsst" | "stroop";
  name: string;
  seconds: number;
  instruction: string;
  metric: string;
  metricUnit: string;
  higherIsBetter: boolean;
  source: {
    author: string;
    year: number;
    journal: string;
    doi: string;
    grade: "A" | "B" | "C";
  } | null;
  tint: Tint;
}

export interface PassiveMeasure {
  id: string;
  name: string;
  line: string;
  status: "coming";
  source?: TestDef["source"];
}

// Seeded last values are placeholders. lastTested is an ISO date or null.
// DunedinPACE seeds value 0.7 with note "1.0 = normal pace".
export interface Biomarker {
  id: string;
  name: string;
  unit: string;
  group: string;
  note?: string;
  seeded: { value: number | null; lastTested: string | null };
}

// connected is a placeholder: false except the glasses, which are true.
export interface Device {
  id: string;
  name: string;
  feeds: string;
  api: string;
  connected: boolean;
  tint: Tint;
  icon: IconName;
}

export type Talkativeness = "rare" | "normal" | "chatty";

export interface ConciergeSettings {
  talkativeness: Talkativeness;
  quietStart: string;
  quietEnd: string;
  voiceOnGlasses: boolean;
  mayAddWalk: boolean;
  mayShieldApps: boolean;
  persona: string;
}

export interface FindStep {
  id: "goal" | "wake" | "caffeine" | "treatments" | "devices" | "quiet";
  question: string;
  options?: { id: string; label: string; icon: IconName }[];
  multi?: boolean;
}
