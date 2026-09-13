import Link from "next/link";
import { LogEntry } from "@/components/brian/LogEntry";

/** One log entry. The recap is fetched client-side from the pipeline backend,
 *  which lives on a LAN address the Next.js server may not share, so this page
 *  stays a thin shell around a client component. */
export const dynamic = "force-dynamic";

export default async function LogPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
        <Link href="/#logs" className="text-sm no-underline" style={{ color: "#6B6B73" }}>
          ← All logs
        </Link>
        <LogEntry id={id} />
      </main>
    </div>
  );
}
