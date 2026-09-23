import type { PassiveMeasure, TestDef } from "./types"

/** Cognitive tests. Each gets a card, a guided start, a result screen and history. */
export const TESTS: TestDef[] = [
  {
    id: "pvt",
    name: "PVT-B",
    seconds: 180,
    instruction: "Tap when the counter starts",
    metric: "mean reaction time",
    metricUnit: "ms",
    higherIsBetter: false,
    source: {
      author: "Basner",
      year: 2011,
      journal: "Acta Astronaut",
      doi: "10.1016/j.actaastro.2011.07.015",
      grade: "A",
    },
    tint: "sage",
  },
  {
    id: "nback",
    name: "2-back",
    seconds: 120,
    instruction: "Tap when the letter matches the one two back",
    metric: "d′",
    metricUnit: "",
    higherIsBetter: true,
    source: null,
    tint: "slate",
  },
  {
    id: "dsst",
    name: "DSST",
    seconds: 90,
    instruction: "Match each symbol to its digit",
    metric: "correct count",
    metricUnit: "correct",
    higherIsBetter: true,
    source: null,
    tint: "sand",
  },
  {
    id: "stroop",
    name: "Stroop",
    seconds: 60,
    instruction: "Tap the ink colour, not the word",
    metric: "interference",
    metricUnit: "ms",
    higherIsBetter: false,
    source: null,
    tint: "bluegrey",
  },
]

/** The three PVT-B numbers shown on its result screen, in order. */
export const METRICS_PVT: string[] = ["mean RT", "lapses over 355 ms", "false starts"]

/** Guided start, one short line per step, keyed by test id. */
export const TEST_STEPS: Record<TestDef["id"], string[]> = {
  pvt: ["Hold the phone in one hand", "Watch the counter", "Tap the moment it starts moving"],
  nback: ["Letters appear one at a time", "Tap when a letter matches the one two back", "Ignore everything else"],
  dsst: ["A key pairs symbols with digits", "For each symbol, tap its digit", "Go as fast as you can without errors"],
  stroop: ["Words appear in coloured ink", "Tap the ink colour, not the word", "Speed and accuracy both count"],
}

/** Label above the comparison number on every result screen. */
export const RESULT_MEDIAN_LABEL = "Your 7-day median"

/** Passive measures. Placeholder cards until the signals land. */
export const PASSIVE: PassiveMeasure[] = [
  {
    id: "typing",
    name: "Typing dynamics",
    line: "Keystroke timing, r≈0.87 with a neuropsych battery",
    status: "coming",
    source: {
      author: "Dagum",
      year: 2018,
      journal: "npj Digit Med",
      doi: "10.1038/s41746-018-0018-4",
      grade: "B",
    },
  },
  {
    id: "pickups",
    name: "Phone pickups",
    line: "How often the phone comes out, from screen time",
    status: "coming",
  },
  {
    id: "reply",
    name: "Reply latency",
    line: "How long messages wait for an answer",
    status: "coming",
  },
]

export interface MindCheckPrompt {
  time: string
  opens: TestDef["id"]
  line: string
}

/** The daily prompt on Today that opens PVT-B. */
export const MIND_CHECK_PROMPT: MindCheckPrompt = {
  time: "10:00",
  opens: "pvt",
  line: "Mind check, 3 minutes",
}
