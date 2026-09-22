import {
  AudioLines,
  BadgeCheck,
  Coffee,
  Footprints,
  Heart,
  MessageCircleQuestion,
  Monitor,
  Moon,
  Pill,
  Sun,
  Users,
  Utensils,
  Wine,
  type LucideIcon,
} from "lucide-react";
import type { Family, Outcome } from "@/lib/today";

/** One icon per meaning (brian-ios-design "Icons", lucide twins of the SF Symbols). */
export const FAMILY_ICONS: Record<Family, LucideIcon> = {
  food: Utensils,
  caffeine: Coffee,
  alcohol: Wine,
  outdoor: Sun,
  screen: Monitor,
  people: Users,
  biometric: Heart,
  medication: Pill,
  winddown: Moon,
  walk: Footprints,
};

/** Held back has no icon: muted words only. */
export const OUTCOME_ICONS: Record<Exclude<Outcome, "held back">, LucideIcon> = {
  whispered: AudioLines,
  asked: MessageCircleQuestion,
  acted: BadgeCheck,
};
