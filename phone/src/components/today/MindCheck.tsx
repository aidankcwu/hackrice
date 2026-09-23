"use client";

import { Button, Card, MEANING_ICONS } from "@/components/ui";
import { useTestStore } from "@/lib/tests";
import { mindCheckDue } from "@/lib/today";

/**
 * The mind check card, from 10:00 until a PVT result exists for today. `until`
 * is the month's last day's clock; null falls back to the phone's clock.
 */
export function MindCheck({ until, query }: { until: number | null; query: string }) {
  const tests = useTestStore();
  if (!tests.loaded || !mindCheckDue(until, tests.store)) return null;
  return (
    <section aria-label="Mind check" className="mt-section">
      <Card
        tint="slate"
        icon={MEANING_ICONS.mind}
        title="Mind check"
        line="3 minutes"
        action={
          <Button variant="tertiary" href={`/tests/pvt${query}`}>
            Take the test
          </Button>
        }
      />
    </section>
  );
}
