"use client";

import { Pencil } from "lucide-react";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Button, EmptyState, ErrorState, InsetList, ListRow, MEANING_ICONS } from "@/components/ui";
import { api, evidenceFrameUrl, type ApiError } from "@/lib/api";
import {
  buildLedger,
  clockTime,
  decisionOutcome,
  errorSentence,
  findEntry,
  reportedText,
  sentence,
  toApiError,
  type LedgerEntry,
} from "@/lib/today";
import type { Decision, DecisionAction } from "@/lib/types";
import { OUTCOME_ICONS } from "./icons";
import { OutcomeLabel } from "./OutcomeLabel";

interface Loaded {
  entry: LedgerEntry | null;
  error: ApiError | null;
}

/**
 * DecisionDetailView: one ledger row opened. What the backend made of it, what it
 * did, the evidence frame it kept, and what the wearer answered. Read once on open.
 */
export function DecisionDetail({ id, theme, scale }: { id: string; theme?: "light" | "dark"; scale?: number }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([api.episodes(), api.decisions(50)])
      .then(([episodes, decisions]) => ({ entry: findEntry(buildLedger(episodes, decisions), id), error: null }))
      .catch((reason: unknown) => ({ entry: null, error: toApiError(reason) }))
      .then((result: Loaded) => {
        if (alive) setLoaded(result);
      });
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  const entry = loaded?.entry ?? null;
  return (
    <Shell screen="today" pushed title={entry ? sentence(entry.label) : ""} theme={theme} scale={scale}>
      {loaded?.error ? (
        <ErrorState
          sentence={errorSentence(loaded.error)}
          action={<Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>}
        />
      ) : null}
      {loaded && !loaded.error && !entry ? <EmptyState text="No longer in today’s log." /> : null}
      {entry ? <EntryBody entry={entry} /> : null}
    </Shell>
  );
}

function EntryBody({ entry }: { entry: LedgerEntry }) {
  const decision = entry.decisions[0];
  const interpretation =
    decision && decision.interpretation.toLowerCase() !== entry.label.toLowerCase()
      ? sentence(decision.interpretation)
      : null;
  const actions = entry.decisions.flatMap((d) => d.actions.map((action) => ({ decision: d, action })));
  const reported = entry.episode?.reported ? reportedText(entry.episode.reported) : "";

  return (
    <>
      {/* A whisper, question or act carries its outcome chip on its own row below. */}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="type-secondary text-muted tabular-nums">{clockTime(entry.t)}</span>
        {entry.outcome === "held back" ? <OutcomeLabel outcome="held back" /> : null}
      </div>

      {interpretation ? <p className="type-body m-0 mt-section text-text">{interpretation}</p> : null}

      <ActionList items={actions} />

      <EvidenceThumbnail decisions={entry.decisions} alt={sentence(entry.label)} />

      {reported ? (
        <div className="mt-section">
          <InsetList label="Wearer reported">
            <ListRow icon={MEANING_ICONS.people} title={reported} />
          </InsetList>
        </div>
      ) : null}
    </>
  );
}

/**
 * What was done, as an inset list: whispers and questions word for word from the
 * backend, acts, and logged notes. The backend's own bookkeeping (`annotate`,
 * `watch`) is left out.
 */
function ActionList({ items }: { items: { decision: Decision; action: DecisionAction }[] }) {
  const shown = items.filter(({ action }) => action.type !== "annotate" && action.type !== "watch");
  if (shown.length === 0) return null;
  return (
    <div className="mt-section">
      <InsetList label="What it did">
        {shown.map(({ decision, action }, index) => (
          <ActionRow key={`${decision.id}-${index}`} decision={decision} action={action} />
        ))}
      </InsetList>
    </div>
  );
}

function ActionRow({ decision, action }: { decision: Decision; action: DecisionAction }) {
  if (action.type === "speak" || action.type === "ask") {
    // Only a line that reached the wearer carries its outcome word; one that was not said is held back.
    const outcome = decisionOutcome(decision) === "held back" ? "held back" : action.type === "ask" ? "asked" : "whispered";
    return <ListRow icon={OUTCOME_ICONS[outcome]} title={action.text} detail={sentence(outcome)} />;
  }
  if (action.type === "act") return <ListRow icon={OUTCOME_ICONS.acted} title={action.kind} detail="Acted" />;
  if (action.type === "log_insight") return <ListRow icon={Pencil} title={action.text} detail="Logged" />;
  return null;
}

/**
 * The newest saved frame of the entry's decisions, fetched into memory and shown;
 * never written to disk. Nothing is drawn when the backend kept no frame.
 */
function EvidenceThumbnail({ decisions, alt }: { decisions: Decision[]; alt: string }) {
  const [frame, setFrame] = useState<{ key: string; url: string } | null>(null);
  const key = decisions.map((d) => d.id).join(",");

  useEffect(() => {
    let alive = true;
    let url: string | null = null;
    (async () => {
      for (const decision of decisions) {
        const frames = await api.evidence(decision.id).catch(() => []);
        const last = frames[frames.length - 1];
        if (!last) continue;
        url = await evidenceFrameUrl(decision.id, last.frame_ref).catch(() => null);
        if (!url) continue;
        if (alive) setFrame({ key, url });
        else URL.revokeObjectURL(url);
        return;
      }
    })();
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
    // `key` names the decisions; the array itself is a new object on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!frame || frame.key !== key) return null;
  return (
    // An in-memory object URL from an authorised fetch: next/image cannot load it.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={frame.url} alt={alt} className="mt-section block aspect-[4/3] w-full rounded-card bg-surface object-cover" />
  );
}
