import type { ConciergeSettings, Talkativeness } from "./types";

// Settings for the whisper agent. Persona is the wearer's own words and starts empty.
export const DEFAULT_CONCIERGE: ConciergeSettings = {
  talkativeness: "normal",
  quietStart: "21:30",
  quietEnd: "07:00",
  voiceOnGlasses: true,
  mayAddWalk: true,
  mayShieldApps: true,
  persona: "",
};

export interface TalkativenessOption {
  id: Talkativeness;
  label: string;
  line: string;
}

export const TALKATIVENESS: TalkativenessOption[] = [
  {
    id: "rare",
    label: "Rare",
    line: "Only when a window is about to close",
  },
  {
    id: "normal",
    label: "Normal",
    line: "A nudge at each window and a note when you finish",
  },
  {
    id: "chatty",
    label: "Chatty",
    line: "Adds context, small wins and a line at night",
  },
];

export type PermissionId = "mayAddWalk" | "mayShieldApps";

export interface PermissionOption {
  id: PermissionId;
  label: string;
  line: string;
}

export const PERMISSIONS: PermissionOption[] = [
  {
    id: "mayAddWalk",
    label: "Add a walk to the calendar",
    line: "Books a short walk when the day has room for one",
  },
  {
    id: "mayShieldApps",
    label: "Shield apps at wind-down",
    line: "Hides feeds and video from screens-off until wake",
  },
];

export const PERSONA_PLACEHOLDER = "In your words: how should it talk to you";
