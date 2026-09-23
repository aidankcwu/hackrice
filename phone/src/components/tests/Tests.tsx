import { Keyboard, MessageSquare, Smartphone, type LucideIcon } from "lucide-react";
import { Button, Card, MEANING_ICONS, type Tint } from "@/components/ui";
import { PASSIVE, TESTS } from "@/content/tests";
import type { PassiveMeasure, TestDef } from "@/content/types";
import { durationLabel } from "@/lib/tests/engine";

/** The metric line under each test's name. */
const METRIC_LINE: Record<TestDef["id"], string> = {
  pvt: "Mean reaction time, lapses over 355 ms, false starts",
  nback: "d′ from hits and false alarms",
  dsst: "Correct count in 90 s",
  stroop: "Interference in ms",
};

/** Passive measures use icons outside MEANING_ICONS, so no app meaning is shared. */
const PASSIVE_LOOK: Record<PassiveMeasure["id"], { tint: Tint; icon: LucideIcon }> = {
  typing: { tint: "stone", icon: Keyboard },
  pickups: { tint: "slate", icon: Smartphone },
  reply: { tint: "sand", icon: MessageSquare },
};

function sourceLine(source: NonNullable<TestDef["source"]>): string {
  return `${source.author} ${source.year} · ${source.journal}`;
}

/**
 * The Tests screen: one card per cognitive test with its duration chip and a
 * "Start" that opens the guided start, then the passive measures still to come.
 */
export function Tests() {
  return (
    <div className="mt-2">
      <div className="flex flex-col gap-3">
        {TESTS.map((test) => (
          <div key={test.id}>
            <Card
              href={`/tests/${test.id}`}
              tint={test.tint}
              icon={MEANING_ICONS.test}
              title={test.name}
              line={METRIC_LINE[test.id]}
              chip={durationLabel(test.seconds)}
              action={<Button variant="tertiary">Start</Button>}
            />
            {test.source ? <p className="type-caption m-0 mt-2 px-card text-muted">{sourceLine(test.source)}</p> : null}
          </div>
        ))}
      </div>

      <h2 className="type-section m-0 mt-section text-ink">Passive measures</h2>
      <div className="mt-3 flex flex-col gap-3">
        {PASSIVE.map((measure) => {
          const look = PASSIVE_LOOK[measure.id] ?? { tint: "stone", icon: MEANING_ICONS.test };
          return (
            <div key={measure.id}>
              <Card tint={look.tint} icon={look.icon} title={measure.name} line={measure.line} chip="Coming" />
              {measure.source ? <p className="type-caption m-0 mt-2 px-card text-muted">{sourceLine(measure.source)}</p> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
