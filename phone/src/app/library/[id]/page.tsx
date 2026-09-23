import { Shell } from "@/components/Shell";
import { TemplateDetail } from "@/components/library/TemplateDetail";
import { Button, EmptyState } from "@/components/ui";
import { protocolById } from "@/content/protocols";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** One template's day: `/library/<template id>`, pushed from the library. */
export default async function TemplatePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const search = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(search) : undefined;
  const query = FIXTURES ? fixtureQuery(search) : "";
  const template = protocolById(id);

  if (!template) {
    return (
      <Shell screen="library" pushed title="Protocol library" theme={view?.theme} scale={view?.scale}>
        <EmptyState text="There is no template with this name." action={<Button href={`/library${query}`}>Back to the library</Button>} />
      </Shell>
    );
  }

  return (
    <Shell screen="library" pushed title={template.name} theme={view?.theme} scale={view?.scale}>
      <TemplateDetail template={template} query={query} />
    </Shell>
  );
}
