/**
 * The Treatments catalogue: 24 cards in the product owner's order, each with
 * the protocol item that "Add to protocol" adds. Plain data, no React.
 *
 * No dose amount appears anywhere. Where a dose exists, `item.clinicianDose`
 * is true and the screen shows `CLINICIAN_DOSE_LINE`.
 */
import type {
  IconName,
  ProtocolItemTemplate,
  Tint,
  Treatment,
  TreatmentCategory,
  TreatmentStatus,
  Weekday,
} from "./types";

/** The only dose text the app ever shows; one source of truth in protocols.ts. */
export { CLINICIAN_DOSE_LINE } from "./protocols";

/** The filter row above the cards, in order. */
export const CATEGORY_CHIPS: { id: TreatmentCategory | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "longevity", label: "Longevity" },
  { id: "weight", label: "Weight" },
  { id: "recovery", label: "Recovery" },
  { id: "sleep", label: "Sleep" },
  { id: "hormones", label: "Hormones" },
  { id: "supplements", label: "Supplements" },
];

/** The chip on a treatment card, per category. */
export const CATEGORY_CARD_CHIP: Record<TreatmentCategory, string> = {
  longevity: "Support longevity",
  weight: "Lose weight",
  recovery: "Recover",
  sleep: "Sleep",
  hormones: "Hormones",
  supplements: "Supplements",
};

/** The status word on a card. */
export const STATUS_LABEL: Record<TreatmentStatus, string> = {
  approved: "Approved",
  "approved off-label": "Approved off-label",
  compounded: "Compounded",
  "research-grade": "Research-grade",
  supplement: "Supplement",
};

/** One tint per category; a two-category item takes its first category's tint. */
export const CATEGORY_TINT: Record<TreatmentCategory, Tint> = {
  longevity: "sage",
  weight: "sand",
  recovery: "stone",
  sleep: "slate",
  hormones: "bluegrey",
  supplements: "stone",
};

const EVERY_DAY: Weekday[] = [0, 1, 2, 3, 4, 5, 6];
const MON: Weekday[] = [0];
const MON_THU: Weekday[] = [0, 3];
const MON_WED_FRI: Weekday[] = [0, 2, 4];

const INJECT: IconName = "peptide";
const ORAL: IconName = "pill";

function tx(
  card: Omit<Treatment, "tint" | "icon" | "item"> & { icon: IconName },
  item: Omit<ProtocolItemTemplate, "id" | "name">,
): Treatment {
  return {
    ...card,
    tint: CATEGORY_TINT[card.categories[0]],
    item: { id: `tx-${card.id}`, name: card.name, ...item },
  };
}

export const TREATMENTS: Treatment[] = [
  tx(
    {
      id: "semaglutide",
      name: "Semaglutide",
      categories: ["weight"],
      timing: "Weekly, same day",
      route: "Pen or tablet",
      cameraSees: "pen",
      cameraLine: "a pen, or a blister pack",
      status: "approved",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "08:00", end: "12:00" },
      days: MON,
      cycle: { weekly: 0 },
      verify: "pen",
      clinicianDose: true,
      note: "Same day each week",
    },
  ),
  tx(
    {
      id: "tirzepatide",
      name: "Tirzepatide",
      categories: ["weight"],
      timing: "Weekly, same day",
      route: "Pen",
      cameraSees: "pen",
      cameraLine: "a pen",
      status: "approved",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "08:00", end: "12:00" },
      days: MON,
      cycle: { weekly: 0 },
      verify: "pen",
      clinicianDose: true,
      note: "Same day each week",
    },
  ),
  tx(
    {
      id: "metformin",
      name: "Metformin",
      categories: ["longevity"],
      timing: "With dinner",
      route: "Tablet",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "approved",
      icon: ORAL,
    },
    {
      kind: "dose",
      window: { start: "18:00", end: "20:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: true,
      note: "With food",
    },
  ),
  tx(
    {
      id: "rapamycin",
      name: "Rapamycin",
      categories: ["longevity"],
      timing: "Weekly",
      route: "Tablet",
      cameraSees: "blister",
      cameraLine: "a blister pack",
      status: "approved off-label",
      icon: ORAL,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: MON,
      cycle: { weekly: 0 },
      verify: "blister",
      clinicianDose: true,
      note: "Same day each week",
    },
  ),
  tx(
    {
      id: "nad-plus",
      name: "NAD+",
      categories: ["longevity"],
      timing: "Weekly, or a few times a week",
      route: "Vial and syringe",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "compounded",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: MON_THU,
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Days set with your clinician",
    },
  ),
  tx(
    {
      id: "nmn-nr",
      name: "NMN and NR",
      categories: ["longevity", "supplements"],
      timing: "Morning, fasted",
      route: "Capsule",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "06:00", end: "08:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: false,
      note: "Before the first meal",
    },
  ),
  tx(
    {
      id: "bpc-157",
      name: "BPC-157",
      categories: ["recovery"],
      timing: "Twice daily, in cycles",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "research-grade",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "09:00" },
      days: EVERY_DAY,
      cycle: { onDays: 28, offDays: 28 },
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Morning window; the evening window is added in review",
    },
  ),
  tx(
    {
      id: "tb-500",
      name: "TB-500",
      categories: ["recovery"],
      timing: "Twice weekly, in cycles",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "research-grade",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: MON_THU,
      cycle: { onDays: 42, offDays: 42 },
      verify: "vial_syringe",
      clinicianDose: true,
    },
  ),
  tx(
    {
      id: "cjc-1295-ipamorelin",
      name: "CJC-1295/ipamorelin",
      categories: ["sleep"],
      timing: "Bedtime",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "compounded",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "21:30", end: "23:00" },
      days: EVERY_DAY,
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Away from food",
    },
  ),
  tx(
    {
      id: "sermorelin",
      name: "Sermorelin",
      categories: ["sleep"],
      timing: "Bedtime",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "compounded",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "21:30", end: "23:00" },
      days: EVERY_DAY,
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Away from food",
    },
  ),
  tx(
    {
      id: "tesamorelin",
      name: "Tesamorelin",
      categories: ["hormones"],
      timing: "Evening",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "approved off-label",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "19:00", end: "21:00" },
      days: EVERY_DAY,
      verify: "vial_syringe",
      clinicianDose: true,
    },
  ),
  tx(
    {
      id: "ghk-cu",
      name: "GHK-Cu",
      categories: ["recovery"],
      timing: "Daily, or in cycles",
      route: "Vial or topical",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe, or a tube",
      status: "compounded",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: EVERY_DAY,
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Cycle set with your clinician",
    },
  ),
  tx(
    {
      id: "epitalon",
      name: "Epitalon",
      categories: ["longevity"],
      timing: "Short cycles",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "research-grade",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "21:00", end: "23:00" },
      days: EVERY_DAY,
      cycle: { onDays: 10, offDays: 170 },
      verify: "vial_syringe",
      clinicianDose: true,
      note: "A short course, then a long break",
    },
  ),
  tx(
    {
      id: "mots-c",
      name: "MOTS-c",
      categories: ["longevity"],
      timing: "2–3 times a week, in cycles",
      route: "Vial",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe",
      status: "research-grade",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: MON_WED_FRI,
      cycle: { onDays: 28, offDays: 28 },
      verify: "vial_syringe",
      clinicianDose: true,
    },
  ),
  tx(
    {
      id: "testosterone-hrt",
      name: "Testosterone or HRT",
      categories: ["hormones"],
      timing: "Weekly injection, or daily topical",
      route: "Injection or topical",
      cameraSees: "vial_syringe",
      cameraLine: "a vial and a syringe, or a tube",
      status: "approved",
      icon: INJECT,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "10:00" },
      days: MON,
      cycle: { weekly: 0 },
      verify: "vial_syringe",
      clinicianDose: true,
      note: "Topical: switch to every day in review",
    },
  ),
  tx(
    {
      id: "tadalafil",
      name: "Tadalafil, daily low dose",
      categories: ["hormones"],
      timing: "Fixed time, daily",
      route: "Tablet",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "approved",
      icon: ORAL,
    },
    {
      kind: "dose",
      window: { start: "08:00", end: "09:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: true,
      note: "Same time each day",
    },
  ),
  tx(
    {
      id: "acarbose",
      name: "Acarbose",
      categories: ["longevity"],
      timing: "First bite of a meal",
      route: "Tablet",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "approved",
      icon: ORAL,
    },
    {
      kind: "dose",
      window: { start: "18:00", end: "20:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: true,
      note: "With the first bite",
    },
  ),
  tx(
    {
      id: "empagliflozin",
      name: "Empagliflozin",
      categories: ["longevity"],
      timing: "Morning",
      route: "Tablet",
      cameraSees: "blister",
      cameraLine: "a blister pack",
      status: "approved",
      icon: ORAL,
    },
    {
      kind: "dose",
      window: { start: "07:00", end: "09:00" },
      days: EVERY_DAY,
      verify: "blister",
      clinicianDose: true,
    },
  ),
  tx(
    {
      id: "creatine",
      name: "Creatine",
      categories: ["recovery", "supplements"],
      timing: "Daily",
      route: "Powder",
      cameraSees: "powder_tub",
      cameraLine: "a powder tub",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "08:00", end: "12:00" },
      days: EVERY_DAY,
      verify: "powder_tub",
      clinicianDose: false,
      note: "Any time of day, every day",
    },
  ),
  tx(
    {
      id: "omega-3",
      name: "Omega-3",
      categories: ["supplements"],
      timing: "With a meal",
      route: "Softgel",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "11:00", end: "13:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: false,
      note: "With food",
    },
  ),
  tx(
    {
      id: "vitamin-d",
      name: "Vitamin D",
      categories: ["supplements"],
      timing: "Morning, with fat",
      route: "Softgel or drops",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "07:00", end: "09:00" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: false,
      note: "With a meal that has fat in it",
    },
  ),
  tx(
    {
      id: "magnesium-glycine",
      name: "Magnesium and glycine",
      categories: ["sleep", "supplements"],
      timing: "Bedtime",
      route: "Capsule or powder",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle, or a powder tub",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "21:00", end: "22:30" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: false,
    },
  ),
  tx(
    {
      id: "taurine",
      name: "Taurine",
      categories: ["supplements"],
      timing: "With a meal",
      route: "Powder or capsule",
      cameraSees: "powder_tub",
      cameraLine: "a powder tub",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "18:00", end: "20:00" },
      days: EVERY_DAY,
      verify: "powder_tub",
      clinicianDose: false,
      note: "With food",
    },
  ),
  tx(
    {
      id: "melatonin",
      name: "Low-dose melatonin",
      categories: ["sleep", "supplements"],
      timing: "Bedtime",
      route: "Tablet",
      cameraSees: "pill_bottle",
      cameraLine: "a pill bottle",
      status: "supplement",
      icon: ORAL,
    },
    {
      kind: "supplement",
      window: { start: "21:30", end: "22:30" },
      days: EVERY_DAY,
      verify: "pill_bottle",
      clinicianDose: false,
    },
  ),
];

/** The cards for one filter chip, in catalogue order. */
export function treatmentsFor(category: TreatmentCategory | "all"): Treatment[] {
  if (category === "all") return TREATMENTS;
  return TREATMENTS.filter((t) => t.categories.includes(category));
}

/** One card by id, or undefined. */
export function treatmentById(id: string): Treatment | undefined {
  return TREATMENTS.find((t) => t.id === id);
}
