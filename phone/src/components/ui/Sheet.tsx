"use client";

import { useEffect, useRef, type ReactNode } from "react";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** 17 semibold, centred. */
  title?: string;
  /** Prefix for the title's element id. */
  id: string;
  children: ReactNode;
}

/**
 * A bottom sheet on the native `<dialog>`: rises from the bottom edge in 280 ms
 * (`dialog.sheet[open]` in globals.css), a grabber at the top, the content
 * scrolls. Escape and a tap on the dimmed page close it.
 */
export function Sheet({ open, onClose, title, id, children }: SheetProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = `${id}-title`;

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  return (
    <dialog
      ref={dialog}
      aria-labelledby={title ? titleId : undefined}
      onClose={onClose}
      // A tap on the dimmed page above the sheet lands on the dialog itself.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className="sheet mx-auto mt-auto mb-0 max-h-[90dvh] w-full max-w-[430px] rounded-t-sheet border-0 bg-surface p-0 text-text backdrop:bg-black/40"
    >
      <div aria-hidden="true" className="mx-auto mt-2 h-[5px] w-9 rounded-full bg-surface-2" />
      <div className="px-card pt-card" style={{ paddingBottom: "max(20px, env(safe-area-inset-bottom))" }}>
        {title ? (
          <h2 id={titleId} className="type-card-title m-0 text-center text-ink">
            {title}
          </h2>
        ) : null}
        {children}
      </div>
    </dialog>
  );
}

export { ActionRow, type ActionRowProps } from "./ActionSheetRows";
