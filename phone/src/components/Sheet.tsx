"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * A bottom sheet on the native `<dialog>`: rises from the bottom edge, a title
 * with Done, the content scrolls. A tap on the dimmed page closes it.
 */
export function Sheet({ open, onClose, title, id, children }: { open: boolean; onClose: () => void; title: string; id: string; children: ReactNode }) {
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
      aria-labelledby={`${id}-title`}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className="sheet mx-auto mt-auto mb-0 max-h-[90dvh] w-full max-w-[430px] rounded-t-panel border-0 bg-surface p-0 text-text backdrop:bg-black/40"
    >
      <div className="px-gutter" style={{ paddingBottom: "max(32px, env(safe-area-inset-bottom))" }}>
        <header className="sticky top-0 z-10 flex min-h-14 items-center justify-center bg-surface">
          <h2 id={`${id}-title`} className="type-body m-0 font-semibold text-ink">
            {title}
          </h2>
          <button type="button" onClick={onClose} className="type-body absolute right-0 min-h-11 font-semibold text-ink">
            Done
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
