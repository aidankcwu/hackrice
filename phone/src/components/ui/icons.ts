import {
  ArrowRight,
  AudioLines,
  BedSingle,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  Coffee,
  Droplets,
  Flame,
  FlaskConical,
  Footprints,
  Glasses,
  HeartPulse,
  ListChecks,
  Menu,
  Monitor,
  Moon,
  Pill,
  Sun,
  Syringe,
  Timer,
  Users,
  Utensils,
  Watch,
  Wind,
  Wine,
  X,
  type LucideIcon,
} from "lucide-react";

/** One icon per meaning, app-wide. Pick by meaning, never by look. */
export const MEANING_ICONS = {
  caffeine: Coffee,
  food: Utensils,
  movement: Footprints,
  light: Sun,
  sleep: Moon,
  screens: Monitor,
  peptide: Syringe,
  pill: Pill,
  alcohol: Wine,
  nap: BedSingle,
  sauna: Flame,
  water: Droplets,
  people: Users,
  air: Wind,
  mind: Brain,
  body: HeartPulse,
  glasses: Glasses,
  watch: Watch,
  test: Timer,
  biomarker: FlaskConical,
  protocol: ListChecks,
  concierge: AudioLines,
  sources: BookOpen,
  menu: Menu,
  close: X,
  chevron: ChevronRight,
  arrow: ArrowRight,
  check: Check,
} satisfies Record<string, LucideIcon>;

export type Meaning = keyof typeof MEANING_ICONS;

/** Icon sizes in px: 18 in rows, 20 in lists, 24 in the tab bar and on cards, 48 on photo panels. */
export const ICON_SIZES = { row: 18, list: 20, card: 24, panel: 48 } as const;

/** The stroke width of every icon. */
export const STROKE = 1.75;
