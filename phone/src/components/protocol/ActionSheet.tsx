"use client";

import { ActionRow, Sheet } from "@/components/ui";

export interface SheetAction {
  id: string;
  label: string;
  destructive?: boolean;
}

export interface ActionSheetProps {
  /** `null` closes the sheet. */
  target: { title: string; line: string; actions: SheetAction[] } | null;
  onChoose: (id: string) => void;
  onClose: () => void;
}

/**
 * A row's action sheet: the item named at the top with its window and status,
 * then its actions as 56 px rows (the destructive one in bad), and Cancel.
 * Escape and a tap on the dimmed page close it too.
 */
export function ActionSheet({ target, onChoose, onClose }: ActionSheetProps) {
  return (
    <Sheet open={target !== null} onClose={onClose} title={target?.title} id="protocol-actions">
      {target ? (
        <>
          <p className="type-secondary m-0 mt-1 mb-3 text-center text-muted tabular-nums">{target.line}</p>
          <div className="-mx-card">
            {target.actions.map((action) => (
              <ActionRow key={action.id} label={action.label} destructive={action.destructive} onClick={() => onChoose(action.id)} />
            ))}
            <ActionRow label="Cancel" onClick={onClose} />
          </div>
        </>
      ) : null}
    </Sheet>
  );
}
