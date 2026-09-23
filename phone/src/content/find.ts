import type {
  Anchored,
  FindStep,
  ProtocolItemTemplate,
  ProtocolTemplate,
  Window,
} from "./types";
import { PROTOCOLS } from "./protocols";

// Six steps. The treatments and devices steps leave options undefined:
// the screen reads TREATMENTS and DEVICES directly.
export const FIND_STEPS: FindStep[] = [
  {
    id: "goal",
    question: "What is this for",
    options: [
      { id: "longer-life", label: "Longer life", icon: "body" },
      { id: "better-days", label: "Better days", icon: "light" },
      { id: "new-parent", label: "New parent", icon: "people" },
      { id: "performance", label: "Performance", icon: "mind" },
    ],
  },
  {
    id: "wake",
    question: "When do you usually wake",
    options: [
      { id: "05:00", label: "05:00", icon: "light" },
      { id: "06:00", label: "06:00", icon: "light" },
      { id: "06:30", label: "06:30", icon: "light" },
      { id: "07:00", label: "07:00", icon: "light" },
      { id: "08:00", label: "08:00", icon: "light" },
    ],
  },
  {
    id: "caffeine",
    question: "How do you take caffeine",
    options: [
      { id: "none", label: "None", icon: "caffeine" },
      { id: "morning", label: "One in the morning", icon: "caffeine" },
      { id: "two-by-lunch", label: "Two, the last by lunch", icon: "caffeine" },
      { id: "afternoon", label: "Afternoon too", icon: "caffeine" },
    ],
  },
  {
    id: "treatments",
    question: "Treatments you take",
    multi: true,
  },
  {
    id: "devices",
    question: "Devices you own",
    multi: true,
  },
  {
    id: "quiet",
    question: "Quiet hours",
    options: [
      { id: "21:00-07:00", label: "21:00–07:00", icon: "sleep" },
      { id: "21:30-07:00", label: "21:30–07:00", icon: "sleep" },
      { id: "22:00-06:30", label: "22:00–06:30", icon: "sleep" },
      { id: "23:00-07:00", label: "23:00–07:00", icon: "sleep" },
    ],
  },
];

export type FindAnswers = Record<string, string | string[]>;

// Treatment ids are matched loosely so a slug like "cjc-1295-ipamorelin"
// or "cjc1295" both count. Only the substrings need to agree with treatments.ts.
const GLP1_KEYS = ["semaglutide", "tirzepatide", "glp"];
const PEPTIDE_KEYS = [
  "bpc",
  "tb-500",
  "tb500",
  "cjc",
  "ipamorelin",
  "sermorelin",
  "tesamorelin",
  "ghk",
  "epitalon",
  "mots",
];

function asList(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

function hasAny(ids: string[], keys: string[]): boolean {
  return ids.some((id) => {
    const slug = id.toLowerCase();
    return keys.some((key) => slug.includes(key));
  });
}

// Template ids as they exist in PROTOCOLS. A short key resolves to the exact
// id when present, else to the first id that starts with it, so "glp1" finds
// "glp1-companion" and "peptide" finds "peptide-adherence".
function resolveTemplateId(key: string): string {
  const exact = PROTOCOLS.find((t) => t.id === key);
  if (exact) return exact.id;
  const prefixed = PROTOCOLS.find((t) => t.id.startsWith(key));
  return prefixed ? prefixed.id : key;
}

export const TEMPLATE_IDS = {
  blueprint: resolveTemplateId("blueprint"),
  sleepFirst: resolveTemplateId("sleep-first"),
  newParent: resolveTemplateId("new-parent"),
  glp1: resolveTemplateId("glp1"),
  peptide: resolveTemplateId("peptide"),
  shiftTravel: resolveTemplateId("shift"),
} as const;

// The closest template for a set of answers, in priority order.
export function pickTemplate(answers: FindAnswers): string {
  const goal = asList(answers.goal)[0];
  const caffeine = asList(answers.caffeine)[0];
  const treatments = asList(answers.treatments);

  if (goal === "new-parent") return TEMPLATE_IDS.newParent;
  if ((goal === "performance" || goal === "better-days") && caffeine === "afternoon") {
    return TEMPLATE_IDS.sleepFirst;
  }
  if (hasAny(treatments, GLP1_KEYS)) return TEMPLATE_IDS.glp1;
  if (hasAny(treatments, PEPTIDE_KEYS)) return TEMPLATE_IDS.peptide;
  if (goal === "longer-life") return TEMPLATE_IDS.blueprint;
  return TEMPLATE_IDS.sleepFirst;
}

const DAY_MIN = 24 * 60;

export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map((part) => Number(part));
  const hours = Number.isFinite(h) ? h : 0;
  const minutes = Number.isFinite(m) ? m : 0;
  return hours * 60 + minutes;
}

export function toHHMM(totalMin: number): string {
  const wrapped = ((totalMin % DAY_MIN) + DAY_MIN) % DAY_MIN;
  const h = Math.floor(wrapped / 60);
  const m = wrapped % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function isClockWindow(window: Window | Anchored): window is Window {
  return "start" in window && "end" in window;
}

// The wake time a template is written around: the end of its bed window
// (a sleep item that crosses midnight), else the earliest clock start among
// its items. A daytime nap is also kind "sleep" but never crosses midnight.
export function templateWake(template: ProtocolTemplate): string | null {
  for (const item of template.items) {
    if (item.kind !== "sleep" || !isClockWindow(item.window)) continue;
    if (toMinutes(item.window.start) > toMinutes(item.window.end)) {
      return item.window.end;
    }
  }

  let earliest: number | null = null;
  for (const item of template.items) {
    if (!isClockWindow(item.window)) continue;
    const start = toMinutes(item.window.start);
    if (earliest === null || start < earliest) earliest = start;
  }
  return earliest === null ? null : toHHMM(earliest);
}

function shiftItem(item: ProtocolItemTemplate, deltaMin: number): ProtocolItemTemplate {
  if (!isClockWindow(item.window)) return { ...item };
  return {
    ...item,
    window: {
      start: toHHMM(toMinutes(item.window.start) + deltaMin),
      end: toHHMM(toMinutes(item.window.end) + deltaMin),
    },
  };
}

// A copy of the template with every clock window moved by the difference
// between the wearer's wake and the template's wake. Anchored windows and
// templates that anchor to actual sleep are returned unshifted.
export function shiftWindows(
  templateId: string,
  wake: string,
  templates: ProtocolTemplate[] = PROTOCOLS,
): ProtocolTemplate | undefined {
  const template = templates.find((t) => t.id === templateId);
  if (!template) return undefined;

  const base = templateWake(template);
  if (base === null || template.flags.anchorToActualSleep) {
    return { ...template, items: template.items.map((item) => ({ ...item })) };
  }

  const deltaMin = toMinutes(wake) - toMinutes(base);
  return {
    ...template,
    items: template.items.map((item) => shiftItem(item, deltaMin)),
  };
}

// The full result of the flow: the picked template shifted to the wearer's wake.
export function buildProtocol(answers: FindAnswers): ProtocolTemplate | undefined {
  const wake = asList(answers.wake)[0] ?? "06:30";
  return shiftWindows(pickTemplate(answers), wake);
}
