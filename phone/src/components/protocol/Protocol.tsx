"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, Chip, EmptyState, ErrorState, InsetList, ListRow, LoadingState, MEANING_ICONS, STROKE, type ChipTone } from "@/components/ui";
import type { ItemKind } from "@/content/types";
import { evidenceFrameUrl } from "@/lib/api";
import type { Day } from "@/lib/month/types";
import { useMyProtocol, writeMyProtocol, type MyProtocolItem } from "@/lib/myProtocol";
import {
  ACTION_LABELS,
  SECTIONS,
  backendSection,
  daysText,
  localSection,
  localStatusText,
  localWindowText,
  minutesOf,
  resolveWindow,
  statusFor,
  statusText,
  toggleAction,
  useDoneMap,
  weekdayOf,
  windowText,
  type LocalStatus,
  type RowAction,
  type Section,
} from "@/lib/protocol";
import { WINDOWS } from "@/lib/rules";
import { errorSentence } from "@/lib/today";
import type { ProtocolKind, ProtocolStatus, ProtocolTodayItem } from "@/lib/types";
import { useMonth } from "@/lib/useMonth";
import { useProtocol } from "@/lib/useProtocol";
import { ActionSheet, type SheetAction } from "./ActionSheet";
import { AddItemSheet } from "./AddItemSheet";
import { DailyAmounts } from "./DailyAmounts";

const EMPTY = "No items yet. Add the first dose window.";

const BACKEND_ICONS: Record<ProtocolKind, keyof typeof MEANING_ICONS> = {
  dose: "pill",
  meal: "food",
  winddown: "sleep",
  walk: "movement",
};

const LOCAL_ICONS: Record<ItemKind, keyof typeof MEANING_ICONS> = {
  dose: "peptide",
  supplement: "pill",
  meal: "food",
  move: "movement",
  sauna: "sauna",
  light: "light",
  sleep: "sleep",
  screens: "screens",
  hydration: "water",
};

const TONE: Record<LocalStatus["status"] | ProtocolStatus, ChipTone> = {
  seen: "good",
  done: "good",
  missed: "bad",
  waiting: "neutral",
  undone: "neutral",
};

/** One row of the screen: a backend item or a local My protocol item, both with today's status. */
type Row =
  | { source: "backend"; id: string; section: Section; start: number; item: ProtocolTodayItem }
  | { source: "local"; id: string; section: Section; start: number; item: MyProtocolItem; status: LocalStatus };

/** The toolbar "+": a 44 px icon button beside the screen title, opening Add item. */
export function AddItemLink({ query }: { query: string }) {
  return (
    <Link
      href={`/protocol/add${query}`}
      aria-label="Add item"
      className="grid size-11 shrink-0 place-items-center rounded-full text-ink motion active:bg-surface-2"
    >
      <Plus size={24} strokeWidth={STROKE} aria-hidden="true" />
    </Link>
  );
}

/** Minutes after local midnight, now: the fallback clock when no month is served. */
function nowMinutes(): number {
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}

function localDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** A stand-in day for a local item's status when the backend serves no month: nothing seen, the protocol's wake. */
const NO_DAY: Pick<Day, "sleep" | "events"> = {
  sleep: { bed: WINDOWS.sleepStart - 1440, wake: WINDOWS.wake, minutes: 0, deep: 0, rem: 0, fragmented: false, wakings: [], hrv_ms: null, rhr_bpm: null, seeded: true },
  events: [],
};

/**
 * Protocol: today's items from `/api/protocol/today` merged with the local
 * My protocol, grouped by section, each with its status for today; then the
 * daily amounts. A row opens the action sheet: a backend row offers Undo or
 * Mark done and Delete (the backend's writes), a local row Mark done or Undo
 * (the local done map) and Remove from My protocol (the local store).
 */
export function Protocol({ query, openAdd = false }: { query: string; openAdd?: boolean }) {
  const router = useRouter();
  const protocol = useProtocol();
  const mine = useMyProtocol();
  const month = useMonth();
  const { isDone, mark } = useDoneMap();
  const [open, setOpen] = useState<Row | null>(null);
  const [addOpen, setAddOpen] = useState(openAdd);

  const day = month.month?.days.at(-1) ?? null;
  const date = day?.date ?? protocol.today?.day ?? localDate();
  const until = day?.until ?? nowMinutes();

  const rows = useMemo<Row[]>(() => {
    const list: Row[] = [];
    for (const item of protocol.today?.items ?? []) {
      list.push({ source: "backend", id: item.id, section: backendSection(item.kind), start: minutesOf(item.window_start), item });
    }
    if (mine.loaded) {
      const weekday = weekdayOf(date);
      for (const item of mine.protocol.items) {
        const section = localSection(item.kind);
        if (!item.enabled || !section || !item.days.includes(weekday)) continue;
        // With no month there is no sleep record to read, so a sleep item waits.
        const status: LocalStatus = isDone(item.id, date)
          ? { status: "done" }
          : !day && item.kind === "sleep"
            ? { status: "waiting" }
            : statusFor(item, day ?? NO_DAY, until);
        list.push({ source: "local", id: item.id, section, start: resolveWindow(item.window, day ?? NO_DAY).start, item, status });
      }
    }
    return list.sort((a, b) => a.start - b.start);
  }, [protocol.today, mine.loaded, mine.protocol.items, date, day, until, isDone]);

  // Nothing is drawn before the backend's first answer (at most the 15 s timeout) and the store's first read.
  if (!protocol.loaded || !mine.loaded) return <LoadingState line="Reading today’s protocol…" />;

  const { error, pending } = protocol;

  const closeAdd = () => {
    setAddOpen(false);
    if (openAdd) router.replace(`/protocol${query}`);
  };

  const choose = (action: string) => {
    const row = open;
    setOpen(null);
    if (!row) return;
    if (row.source === "backend") {
      void protocol.act(row.item, action as RowAction);
      return;
    }
    if (action === "done") mark(row.id, date, true);
    if (action === "undo") mark(row.id, date, false);
    if (action === "remove") {
      const current = mine.protocol;
      writeMyProtocol({ ...current, items: current.items.filter((item) => item.id !== row.id), updatedAt: new Date().toISOString() });
    }
  };

  const target = open ? sheetTarget(open) : null;
  const sections = SECTIONS.map((section) => ({ ...section, rows: rows.filter((row) => row.section === section.id) })).filter(
    (section) => section.rows.length > 0,
  );

  return (
    <>
      {error ? (
        <ErrorState
          sentence={errorSentence(error)}
          action={<Button onClick={protocol.retry}>Try again</Button>}
        />
      ) : null}

      {!error && rows.length === 0 ? (
        <EmptyState text={EMPTY} action={<Button onClick={() => setAddOpen(true)}>Add item</Button>} />
      ) : null}

      {sections.map((section, index) => {
        const headingId = `protocol-${section.id}`;
        return (
          <section key={section.id} aria-labelledby={headingId} className={index === 0 && !error ? "mt-2" : "mt-section"}>
            <h2 id={headingId} className="type-section m-0 text-ink">
              {section.title}
            </h2>
            <div className="mt-3">
              <InsetList>
                {section.rows.map((row) => (
                  <ProtocolRow key={`${row.source}-${row.id}`} row={row} busy={row.source === "backend" && pending === row.id} onOpen={() => setOpen(row)} />
                ))}
              </InsetList>
            </div>
          </section>
        );
      })}

      <DailyAmounts day={day} />

      <ActionSheet target={target} onChoose={choose} onClose={() => setOpen(null)} />
      <AddItemSheet
        open={addOpen}
        onClose={closeAdd}
        onAdded={() => {
          void protocol.refresh();
          closeAdd();
        }}
      />
    </>
  );
}

/** What the action sheet names and offers for a row. */
function sheetTarget(row: Row): { title: string; line: string; actions: SheetAction[] } {
  if (row.source === "backend") {
    const first = toggleAction(row.item.status);
    return {
      title: row.item.name,
      line: `${windowText(row.item)} · ${statusText(row.item)}`,
      actions: [
        { id: first, label: ACTION_LABELS[first] },
        { id: "delete", label: ACTION_LABELS.delete, destructive: true },
      ],
    };
  }
  const done = row.status.status === "done";
  return {
    title: row.item.name,
    line: `${localWindowText(row.item.window)} · ${localStatusText(row.status)}`,
    actions: [
      done ? { id: "undo", label: "Undo" } : { id: "done", label: "Mark done" },
      { id: "remove", label: "Remove from My protocol", destructive: true },
    ],
  };
}

function ProtocolRow({ row, busy, onOpen }: { row: Row; busy: boolean; onOpen: () => void }) {
  const icon = row.source === "backend" ? MEANING_ICONS[BACKEND_ICONS[row.item.kind]] : MEANING_ICONS[LOCAL_ICONS[row.item.kind]];
  const hours = row.source === "backend" ? windowText(row.item) : localWindowText(row.item.window);
  const days = daysText(row.item.days);
  const detail = days === "Every day" ? hours : `${hours} · ${days}`;
  const status = row.source === "backend" ? statusText(row.item) : localStatusText(row.status);
  const tone = TONE[row.source === "backend" ? row.item.status : row.status.status];
  const evidence = row.source === "backend" && row.item.status === "seen" ? row.item.evidence_ref : null;
  return (
    <ListRow
      icon={icon}
      title={row.item.name}
      detail={detail}
      onClick={busy ? undefined : onOpen}
      trailing={
        <span className={`flex shrink-0 items-center gap-2 ${busy ? "opacity-40" : ""}`}>
          <Chip tone={tone}>{status}</Chip>
          {evidence ? <Thumbnail evidenceRef={evidence} /> : null}
        </span>
      }
    />
  );
}

/**
 * The frame the glasses kept when they saw the item, 40 px, fetched into memory
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
    <img src={frame.url} alt="" className="size-10 shrink-0 rounded-[10px] bg-surface-2 object-cover" />
  );
}
