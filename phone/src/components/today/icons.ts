import { BadgeCheck, EyeOff, MessageCircleQuestion, type LucideIcon } from "lucide-react";
import { MEANING_ICONS } from "@/components/ui";
import type { Family, Outcome } from "@/lib/today";

/** One icon per trigger family, drawn from the app-wide meaning icons. */
export const FAMILY_ICONS: Record<Family, LucideIcon> = {
  food: MEANING_ICONS.food,
  caffeine: MEANING_ICONS.caffeine,
  alcohol: MEANING_ICONS.alcohol,
  outdoor: MEANING_ICONS.light,
  screen: MEANING_ICONS.screens,
  people: MEANING_ICONS.people,
  biometric: MEANING_ICONS.body,
  medication: MEANING_ICONS.pill,
  winddown: MEANING_ICONS.sleep,
  walk: MEANING_ICONS.movement,
};

/** One icon per ledger outcome. */
export const OUTCOME_ICONS: Record<Outcome, LucideIcon> = {
  whispered: MEANING_ICONS.concierge,
  asked: MessageCircleQuestion,
  acted: BadgeCheck,
  "held back": EyeOff,
};
