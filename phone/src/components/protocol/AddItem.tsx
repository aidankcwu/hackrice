import { Shell } from "@/components/Shell";
import { AddItemLink, Protocol } from "./Protocol";

export { AddItemSheet, type AddItemSheetProps } from "./AddItemSheet";

/** The Protocol screen with the Add item sheet open: `/protocol/add`, and `?screen=additem` in fixtures mode. */
export function AddItem({ query, theme, scale }: { query: string; theme?: "light" | "dark"; scale?: number }) {
  return (
    <Shell screen="protocol" theme={theme} scale={scale} action={<AddItemLink query={query} />}>
      <Protocol query={query} openAdd />
    </Shell>
  );
}
