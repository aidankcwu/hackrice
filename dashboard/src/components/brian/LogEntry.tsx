"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { T } from "@/lib/tokens";
import type { Recap } from "@/lib/types";
import { Panel } from "./Panel";
import { SessionReport } from "./SessionReport";

/** Loads one saved recap by id. Fetched here rather than on the server because
 *  the pipeline backend is a LAN address the Next.js process may not reach. */
export function LogEntry({ id }: { id: string }) {
  const [recap, setRecap] = useState<Recap | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");

  useEffect(() => {
    let live = true;
    void api.recapById(id).then((body) => {
      if (!live) return;
      setRecap(body);
      setState(body ? "ready" : "missing");
    });
    return () => { live = false; };
  }, [id]);

  if (state === "loading") {
    return <Panel><p className="m-0 text-sm" style={{ color: T.muted }}>Loading the report…</p></Panel>;
  }
  if (state === "missing" || !recap) {
    return (
      <Panel>
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No report with that id. It may have been recorded against a database that has since
          been reset, or the backend is unreachable.
        </p>
      </Panel>
    );
  }
  return <SessionReport recap={recap} />;
}
