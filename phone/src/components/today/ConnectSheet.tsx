"use client";

import { useEffect, useRef } from "react";

/**
 * The glasses steps from SetupView's Glasses row, as a sheet the user can close.
 * Until Job 1 the glasses app owns the stream, so this page can only point the
 * way; Today turns to "Watching" by itself once the backend sees frames.
 */
const STEPS = [
  "Pair the glasses in Meta AI.",
  "In the glasses app, tap Register, then Start watching.",
  "Put the glasses on. Counting starts the moment the camera is up.",
] as const;

export function ConnectSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="connect-title"
      onClose={onClose}
      // A tap on the dimmed page above the sheet lands on the dialog itself.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className="sheet mx-auto mt-auto mb-0 max-h-[90dvh] w-full max-w-[430px] rounded-t-panel border-0 bg-surface p-0 text-text backdrop:bg-black/40"
    >
      <div className="px-gutter" style={{ paddingBottom: "max(32px, env(safe-area-inset-bottom))" }}>
        <header className="relative flex min-h-14 items-center justify-center">
          <h2 id="connect-title" className="type-body m-0 font-semibold text-ink">
            Connect glasses
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="type-body absolute right-0 min-h-11 font-semibold text-ink"
          >
            Done
          </button>
        </header>
        <ol className="m-0 mt-2 list-none p-0">
          {STEPS.map((step, index) => (
            <li key={step} className="flex gap-4 border-t-[0.5px] border-line py-4 first:border-t-0">
              <span className="type-body w-[calc(16px*var(--type-scale))] shrink-0 text-muted tabular-nums">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="type-body m-0 text-text">{step}</p>
                {index === 0 ? (
                  <a
                    href="fb-viewapp://"
                    className="type-body mt-4 inline-flex min-h-11 items-center rounded-full bg-surface-2 px-4 font-semibold text-ink"
                  >
                    Open Meta AI
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </dialog>
  );
}
