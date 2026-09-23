import { CircleDot, Server, type LucideIcon } from "lucide-react";
import { Button, ICON_SIZES, MEANING_ICONS, STROKE } from "@/components/ui";
import type { ApiError } from "@/lib/api";
import { backendHost, errorSentence, glassesConnected, watchedMinutes } from "@/lib/today";
import type { Session, Status } from "@/lib/types";

/**
 * Three rows 36 tall, noun + state: glasses, backend, watching. When the backend
 * cannot be read, the strip is one problem row on a bad-soft fill with a 36 px
 * "Try again" pill.
 */
export function StatusStrip({
  status,
  session,
  error,
  onRetry,
}: {
  status: Status | null;
  session: Session | null;
  error: ApiError | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <div role="alert" className="flex min-h-9 items-center gap-3 rounded-tile bg-bad-soft py-1 pr-1 pl-3 text-bad">
        <Server size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0" aria-hidden="true" />
        <span className="type-secondary min-w-0 flex-1">{errorSentence(error)}</span>
        <Button variant="secondary" onClick={onRetry} className="min-h-9! shrink-0">
          Try again
        </Button>
      </div>
    );
  }
  if (!status) return null;

  const rows: [LucideIcon, string][] = [
    [MEANING_ICONS.glasses, glassesConnected(status) ? "Glasses connected" : "Glasses off"],
    [Server, `Backend ${backendHost()}`],
    [CircleDot, session ? `Watching ${watchedMinutes(session, status)} min` : "Not watching"],
  ];
  return (
    <ul aria-label="Status" className="m-0 min-w-0 list-none p-0">
      {rows.map(([Icon, text]) => (
        <li key={text} className="type-secondary flex min-h-9 items-center gap-3 text-text">
          <Icon size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0 text-muted" aria-hidden="true" />
          <span className="min-w-0">{text}</span>
        </li>
      ))}
    </ul>
  );
}
