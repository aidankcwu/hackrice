import { CircleDot, Glasses, Server, type LucideIcon } from "lucide-react";
import type { ApiError } from "@/lib/api";
import { backendHost, errorSentence, glassesConnected, watchedMinutes } from "@/lib/today";
import type { Session, Status } from "@/lib/types";

/** Icon box one `.subheadline` line tall, so the symbol sits on the first line when text wraps. */
const ICON_SLOT = "flex h-[calc(20px*var(--type-scale))] shrink-0 items-center";
const ICON = "size-[calc(16px*var(--type-scale))]";

/**
 * Three lines, noun + state: glasses, backend, watching. When the backend cannot
 * be read, the strip collapses to that one problem, in the cost colour, and the
 * primary button below it becomes "Try again".
 */
export function StatusStrip({
  status,
  session,
  error,
}: {
  status: Status | null;
  session: Session | null;
  error: ApiError | null;
}) {
  if (error) {
    return (
      <div role="alert" className="type-secondary flex min-h-[28px] items-start gap-2 py-1 text-cost">
        <span className={ICON_SLOT}>
          <Server className={ICON} strokeWidth={2} aria-hidden="true" />
        </span>
        <span>{errorSentence(error)}</span>
      </div>
    );
  }
  if (!status) return null;

  const rows: [LucideIcon, string][] = [
    [Glasses, glassesConnected(status) ? "Glasses connected" : "Glasses off"],
    [Server, `Backend ${backendHost()}`],
    [CircleDot, session ? `Watching ${watchedMinutes(session, status)} min` : "Not watching"],
  ];
  return (
    <ul aria-label="Status" className="m-0 list-none p-0">
      {rows.map(([Icon, text]) => (
        <li key={text} className="type-secondary flex min-h-[28px] items-start gap-2 py-1 text-text">
          <span className={`${ICON_SLOT} text-muted`}>
            <Icon className={ICON} strokeWidth={2} aria-hidden="true" />
          </span>
          <span>{text}</span>
        </li>
      ))}
    </ul>
  );
}
