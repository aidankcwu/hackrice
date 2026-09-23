import { Shell } from "@/components/Shell";
import { Calendar } from "@/components/calendar/Calendar";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function CalendarPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="calendar" theme={view?.theme} scale={view?.scale}>
      <Calendar />
    </Shell>
  );
}
