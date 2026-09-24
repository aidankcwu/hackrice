"use client";

import { useLayoutEffect } from "react";
import { applyEmbed } from "@/lib/embed";

/**
 * Re-applies embed mode after hydration (src/lib/embed.ts). The `<head>` script
 * already did it before first paint; React's dev remount resets `<html>`'s
 * attributes, and this puts `data-embed` back before the browser paints.
 */
export function EmbedCapture() {
  useLayoutEffect(() => {
    applyEmbed(window);
  }, []);
  return null;
}
