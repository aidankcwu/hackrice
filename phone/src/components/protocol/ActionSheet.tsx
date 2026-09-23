"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { ACTION_LABELS, statusText, toggleAction, windowText, type RowAction } from "@/lib/protocol";
import type { ProtocolTodayItem } from "@/lib/types";

/**
 * The row's action sheet: the item named at the top, Undo (seen, done) or Mark
 * done (waiting, missed), then Delete, and Cancel apart. A native modal dialog:
 * focus stays inside, Escape and a tap on the dimmed page close it.
 */
export function ActionSheet({
  item,
  onChoose,
  onClose,
}: {
  item: ProtocolTodayItem | null;
  onChoose: (action: RowAction) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (item && !element.open) element.showModal();
    if (!item && element.open) element.close();
  }, [item]);

  const first = item ? toggleAction(item.status) : null;

  return (
    <dialog
      ref={dialog}
      aria-labelledby="protocol-sheet-title"
      onClose={onClose}
      // A tap on the dimmed page above the sheet lands on the dialog itself.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className="sheet mx-auto mt-auto mb-0 w-full max-w-[430px] border-0 bg-transparent p-0 text-text backdrop:bg-black/40"
    >
      {item && first ? (
        <div className="px-2" style={{ paddingBottom: "max(8px, env(safe-area-inset-bottom))" }}>
          <div className="overflow-hidden rounded-panel bg-surface">
            <header className="px-4 py-4 text-center">
              <h2 id="protocol-sheet-title" className="type-caption m-0 font-semibold text-muted">
                {item.name}
              </h2>
              <p className="type-caption m-0 text-muted tabular-nums">
                {windowText(item)} · {statusText(item)}
              </p>
            </header>
            <SheetButton onClick={() => onChoose(first)}>{ACTION_LABELS[first]}</SheetButton>
            <SheetButton onClick={() => onChoose("delete")}>{ACTION_LABELS.delete}</SheetButton>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="type-body mt-2 flex min-h-14 w-full items-center justify-center rounded-panel bg-surface font-semibold text-ink transition-colors duration-150 active:bg-surface-2"
          >
            Cancel
          </button>
        </div>
      ) : null}
    </dialog>
  );
}

function SheetButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="type-body flex min-h-14 w-full items-center justify-center border-t-[0.5px] border-line px-4 text-ink transition-colors duration-150 active:bg-surface-2"
    >
      {children}
    </button>
  );
}
