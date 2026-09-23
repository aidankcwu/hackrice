"use client";

import { useRouter } from "next/navigation";
import type { ProtocolTemplate } from "@/content/types";
import { ProtocolReview } from "./ProtocolReview";

export interface ReviewScreenProps {
  template: ProtocolTemplate;
  /** Fixtures mode: the screenshot params to carry onto Protocol. */
  query?: string;
}

/** The library's review page body: the guided review, then on to Protocol. */
export function ReviewScreen({ template, query = "" }: ReviewScreenProps) {
  const router = useRouter();
  return <ProtocolReview template={template} source="template" onDone={() => router.push(`/protocol${query}`)} />;
}
