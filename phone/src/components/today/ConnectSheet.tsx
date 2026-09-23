"use client";

import { Button, InsetList, Sheet } from "@/components/ui";

/**
 * The glasses steps from SetupView's Glasses row, as a sheet the user can close.
 * Until Job 1 the glasses app owns the stream, so this page can only point the
 * way; Today turns to "Watching" by itself once the backend sees frames.
 *
 * The glasses app today is Meta's sample, which Job 1 replaces, so the steps name
 * no buttons. Re-check this wording at Job 1.
 */
const STEPS = [
  "Open Meta AI and check the glasses are paired with Developer Mode on.",
  "Open the glasses app and connect it to the Mac.",
  "Start the stream.",
] as const;

export function ConnectSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onClose={onClose} title="Connect glasses" id="connect">
      <div className="mt-4">
        <InsetList>
          {STEPS.map((step, index) => (
            <li
              key={step}
              className="relative flex min-h-row items-start gap-3 py-4 pr-4 pl-4 not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-4 not-first:before:hairline"
            >
              <span className="type-body w-4 shrink-0 text-muted tabular-nums">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="type-body m-0 text-text">{step}</p>
                {index === 0 ? (
                  <div className="mt-3">
                    <Button variant="secondary" href="fb-viewapp://">
                      Open Meta AI
                    </Button>
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </InsetList>
      </div>
      <div className="mt-4">
        <Button onClick={onClose}>Done</Button>
      </div>
    </Sheet>
  );
}
