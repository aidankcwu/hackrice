import type { Biomarker } from "./types"

// Panel cards for the Biomarkers screen. Seeded values are placeholders for a
// healthy 45-year-old; lastTested is an ISO date or null when never measured.

export const BIOMARKER_TARGET = "set by your clinician" as const

export interface BiomarkerCard extends Biomarker {
  target: typeof BIOMARKER_TARGET
}

export interface BaselineCard {
  title: string
  line: string
}

export const BIOMARKER_GROUPS: string[] = [
  "Lipids",
  "Inflammation",
  "Glucose",
  "Liver and kidney",
  "Micronutrients",
  "Hormones",
  "Pace of aging",
  "Fitness",
  "Recovery",
]

const BLOOD_PANEL_DATE = "2026-08-14"
const WEARABLE_DATE = "2026-09-21"

export const BIOMARKERS: BiomarkerCard[] = [
  {
    id: "apob",
    name: "ApoB",
    unit: "mg/dL",
    group: "Lipids",
    target: BIOMARKER_TARGET,
    seeded: { value: 78, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "ldl_c",
    name: "LDL-C",
    unit: "mg/dL",
    group: "Lipids",
    target: BIOMARKER_TARGET,
    seeded: { value: 92, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "hdl",
    name: "HDL",
    unit: "mg/dL",
    group: "Lipids",
    target: BIOMARKER_TARGET,
    seeded: { value: 58, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "triglycerides",
    name: "Triglycerides",
    unit: "mg/dL",
    group: "Lipids",
    target: BIOMARKER_TARGET,
    seeded: { value: 84, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "hs_crp",
    name: "hs-CRP",
    unit: "mg/L",
    group: "Inflammation",
    target: BIOMARKER_TARGET,
    seeded: { value: 0.6, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "hba1c",
    name: "HbA1c",
    unit: "%",
    group: "Glucose",
    target: BIOMARKER_TARGET,
    seeded: { value: 5.2, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "fasting_glucose",
    name: "Fasting glucose",
    unit: "mg/dL",
    group: "Glucose",
    target: BIOMARKER_TARGET,
    seeded: { value: 86, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "fasting_insulin",
    name: "Fasting insulin",
    unit: "µIU/mL",
    group: "Glucose",
    target: BIOMARKER_TARGET,
    seeded: { value: 5.1, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "alt",
    name: "ALT",
    unit: "U/L",
    group: "Liver and kidney",
    target: BIOMARKER_TARGET,
    seeded: { value: 22, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "egfr",
    name: "eGFR",
    unit: "mL/min/1.73m²",
    group: "Liver and kidney",
    target: BIOMARKER_TARGET,
    seeded: { value: 98, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "vitamin_d",
    name: "Vitamin D",
    unit: "ng/mL",
    group: "Micronutrients",
    target: BIOMARKER_TARGET,
    seeded: { value: 46, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "ferritin",
    name: "Ferritin",
    unit: "ng/mL",
    group: "Micronutrients",
    target: BIOMARKER_TARGET,
    seeded: { value: 88, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "testosterone_or_estradiol",
    name: "Testosterone or estradiol",
    unit: "ng/dL",
    group: "Hormones",
    note: "Testosterone in ng/dL, estradiol in pg/mL",
    target: BIOMARKER_TARGET,
    seeded: { value: 540, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "tsh",
    name: "TSH",
    unit: "mIU/L",
    group: "Hormones",
    target: BIOMARKER_TARGET,
    seeded: { value: 1.8, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "dunedin_pace",
    name: "DunedinPACE",
    unit: "pace",
    group: "Pace of aging",
    note: "1.0 = normal pace; Bryan 0.7",
    target: BIOMARKER_TARGET,
    seeded: { value: 0.7, lastTested: BLOOD_PANEL_DATE },
  },
  {
    id: "vo2max",
    name: "VO2max",
    unit: "mL/kg/min",
    group: "Fitness",
    target: BIOMARKER_TARGET,
    seeded: { value: null, lastTested: null },
  },
  {
    id: "grip_strength",
    name: "Grip strength",
    unit: "kg",
    group: "Fitness",
    target: BIOMARKER_TARGET,
    seeded: { value: null, lastTested: null },
  },
  {
    id: "resting_hr",
    name: "Resting HR",
    unit: "bpm",
    group: "Recovery",
    target: BIOMARKER_TARGET,
    seeded: { value: 56, lastTested: WEARABLE_DATE },
  },
  {
    id: "hrv",
    name: "HRV",
    unit: "ms",
    group: "Recovery",
    target: BIOMARKER_TARGET,
    seeded: { value: 62, lastTested: WEARABLE_DATE },
  },
]

export const BASELINE_CARD: BaselineCard = {
  title: "Get your baseline",
  line: "One blood draw covers the first fifteen.",
}
