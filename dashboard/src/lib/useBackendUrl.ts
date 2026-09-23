"use client";
import { useSyncExternalStore } from "react";
import { backendUrl } from "./runtime";

const subscribe = () => () => {};

/** false during SSR and the hydration pass, true after — without an effect-driven re-render loop. */
export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

/**
 * `backendUrl(pathOrUrl)` once hydrated, undefined before. The server cannot
 * know the browser's backend base (it depends on the page URL behind the proxy)
 * or its token (localStorage), and React keeps a server-rendered attribute that
 * mismatches on hydration, so an image src is only filled in on the client.
 */
export function useBackendUrl(pathOrUrl: string | null | undefined): string | undefined {
  const hydrated = useHydrated();
  return hydrated && pathOrUrl ? backendUrl(pathOrUrl) : undefined;
}
