import type { Analysis } from "@/lib/analysis";

/** Shade for one day of the month strip: green when near the ceiling, red when far under it. */
function shade(level: number, sick: boolean): string {
  if (sick) return "bg-band";
  if (level >= 97) return "bg-earn";
  if (level >= 93) return "bg-earn/60";
  if (level >= 89) return "bg-earn/30";
  if (level >= 85) return "bg-cost/30";
  return "bg-cost/70";
}

/**
 * The reds and what they did, in sentences with numbers, then the averages and
 * the one lever. Month adds a 30-cell strip, one cell per day.
 */
export function AnalysisPanel({ analysis, month }: { analysis: Analysis; month: boolean }) {
  return (
    <section aria-label="What the reds did" className="rounded-panel bg-surface p-panel">
      {analysis.sentences.length ? (
        <ul className="m-0 list-none p-0">
          {analysis.sentences.map((s) => (
            <li key={s} className="type-secondary border-t-[0.5px] border-line py-2 text-text first:border-t-0 first:pt-0">
              {s}
            </li>
          ))}
        </ul>
      ) : (
        <p className="type-secondary m-0 text-text">No reds in this range.</p>
      )}
      <p className="type-body m-0 mt-4 font-semibold text-ink tabular-nums">{analysis.summary}</p>
      {analysis.lever ? <p className="type-secondary m-0 mt-1 text-text">{analysis.lever}</p> : null}
      {month ? (
        <div className="mt-4">
          <div className="grid grid-cols-10 gap-1" role="img" aria-label="The month, one cell per day, shaded by how much of the ceiling was used">
            {analysis.strip.map((cell) => (
              <span
                key={cell.date}
                title={`${cell.date}: ${Math.round(cell.level)}%`}
                className={`aspect-square rounded-[4px] ${shade(cell.level, cell.sick)}`}
              />
            ))}
          </div>
          <p className="type-caption m-0 mt-2 text-muted">Each cell is a day: darker green near the ceiling, red far under it, grey when sick.</p>
        </div>
      ) : null}
    </section>
  );
}
