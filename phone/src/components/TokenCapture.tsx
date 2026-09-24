"use client";

import { useEffect } from "react";
import { accessToken } from "@/lib/runtime";

/**
 * Captures `?token=` into storage and takes it out of the address bar on every
 * page, including the ones that never call the backend (src/lib/runtime.ts).
 */
export function TokenCapture() {
  useEffect(() => {
    accessToken();
  }, []);
  return null;
}
