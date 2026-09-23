"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { FAMILY_ICONS } from "@/components/today/icons";
import { evidenceFrameUrl } from "@/lib/api";
import { kindFamily, statusText, windowText, type RowAction } from "@/lib/protocol";
import { errorSentence } from "@/lib/today";
import type { ProtocolTodayItem } from "@/lib/types";
import { useProtocol } from "@/lib/useProtocol";
import { ActionSheet } from "./ActionSheet";

const EMPTY = "No items yet. Add the first dose window.";

/** The one primary control: a full-width capsule in the prominent glass. */
export const PRIMARY =
  "type-body glass-prominent flex min-h-[50px] w-full items-center justify-center rounded-full px-6 py-2 text-center font-semibold";

/** The toolbar "+", left of the gear: pushes Add item. */
export function AddItemLink({ query }: { query: string }) {
  return (
    <Link
      href={`/protocol/add${query}`}
      aria-label="Add item"
      className="grid size-11 place-items-center rounded-full text-ink"
    >
      <Plus size={24} strokeWidth={2} aria-hidden="true" />
    </Link>
  );
}

/**
 * ProtocolView: today's items from `/api/protocol/today`, earliest window first.
 * Row: kind symbol · name · window · status, with the frame the glasses kept when
 * they saw it. A row opens the action sheet (Undo or Mark done, and Delete), the
 * app's only overlay.
 */
export function Protocol({ query }: { query: string }) {
  const protocol = useProtocol();
  const [open, setOpen] = useState<ProtocolTodayItem | null>(null);

  // Nothing is drawn before the backend's first answer (at most the 15 s timeout).
  if (!protocol.loaded) return null;

  const { today, error, pending } = protocol;
  const items = today?.items ?? null;

  const choose = (action: RowAction) => {
    const item = open;
    setOpen(null);
    if (item) void protocol.act(item, action);
  };

  return (
    <>
      {error ? (
        <>
          <p role="alert" className="type-secondary m-0 mt-2 text-cost">
            {errorSentence(error)}
          </p>
          <button type="button" onClick={protocol.retry} className={`${PRIMARY} mt-4`}>
            Try again
          </button>
        </>
      ) : null}

      {!error && items && items.length === 0 ? (
        <>
          <p className="type-body m-0 mt-2 text-text">{EMPTY}</p>
          <Link href={`/protocol/add${query}`} className={`${PRIMARY} mt-4`}>
            Add item
          </Link>
        </>
      ) : null}

      {items && items.length > 0 ? (
        <ul className={`m-0 list-none border-b-[0.5px] border-line p-0 ${error ? "mt-section" : "mt-2"}`}>
          {items.map((item) => (
            <li key={item.id} className="border-t-[0.5px] border-line">
              <ProtocolRow item={item} busy={pending === item.id} onOpen={() => setOpen(item)} />
            </li>
          ))}
        </ul>
      ) : null}

      <ActionSheet item={open} onChoose={choose} onClose={() => setOpen(null)} />
    </>
  );
}

function ProtocolRow({ item, busy, onOpen }: { item: ProtocolTodayItem; busy: boolean; onOpen: () => void }) {
  const Icon = FAMILY_ICONS[kindFamily(item.kind)];
  const hours = windowText(item);
  const status = statusText(item);
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={busy}
      aria-haspopup="dialog"
      aria-busy={busy || undefined}
      aria-label={`${item.name}, ${hours}, ${status}`}
      className="flex min-h-[52px] w-full items-center py-2 text-left transition-colors duration-150 active:bg-surface-2 disabled:opacity-40"
    >
      <span className="flex w-[calc(18px*var(--type-scale))] shrink-0 justify-center text-muted">
        <Icon className="size-[calc(18px*var(--type-scale))]" strokeWidth={2} aria-hidden="true" />
      </span>
      <span className="ml-4 flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-2">
        <span className="min-w-0">
          <span className="type-body block text-text">{item.name}</span>
          <span className="type-secondary block text-muted tabular-nums">{hours}</span>
        </span>
        <span
          className={`type-outcome whitespace-nowrap tabular-nums ${
            item.status === "waiting" || item.status === "undone" ? "font-normal text-muted" : "text-ink"
          }`}
        >
          {status}
        </span>
      </span>
      {item.status === "seen" && item.evidence_ref ? <Thumbnail evidenceRef={item.evidence_ref} /> : null}
    </button>
  );
}

/**
 * The frame the glasses kept when they saw the item, 44 px, fetched into memory
 * and shown; never written to disk. Nothing is drawn when the backend has none.
 */
function Thumbnail({ evidenceRef }: { evidenceRef: string }) {
  const [frame, setFrame] = useState<{ ref: string; url: string } | null>(null);

  useEffect(() => {
    const [decisionId, ref] = evidenceRef.split("/");
    if (!decisionId || !ref) return;
    let alive = true;
    let url: string | null = null;
    evidenceFrameUrl(decisionId, ref)
      .then((made) => {
        url = made;
        if (alive) setFrame({ ref: evidenceRef, url: made });
        else URL.revokeObjectURL(made);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [evidenceRef]);

  if (!frame || frame.ref !== evidenceRef) return null;
  return (
    // An in-memory object URL from an authorised fetch: next/image cannot load it.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={frame.url} alt="" className="ml-2 size-11 shrink-0 rounded-lg bg-surface object-cover" />
  );
}
