import type { ReactNode } from "react";

export interface LoadingStateProps {
  /** One calm line. */
  line?: string;
  /** Static placeholder blocks under it: the first 96 px, the rest 56. */
  blocks?: number;
}

/** Loading: the line, then still surface blocks. No shimmer. */
export function LoadingState({ line = "Scoring today…", blocks = 3 }: LoadingStateProps) {
  return (
    <div>
      <p role="status" className="type-secondary m-0 text-muted">
        {line}
      </p>
      <div aria-hidden="true" className="mt-4 flex flex-col gap-3">
        {Array.from({ length: Math.max(0, blocks) }, (_, index) => (
          <div key={index} className={`rounded-card bg-surface ${index === 0 ? "h-24" : "h-14"}`} />
        ))}
      </div>
    </div>
  );
}

export interface EmptyStateProps {
  /** An instruction. */
  text: string;
  /** The primary Button. */
  action?: ReactNode;
}

/** Empty: an instruction and the primary button. */
export function EmptyState({ text, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center py-section text-center">
      <p className="type-body m-0 max-w-[32ch] text-text">{text}</p>
      {action ? <div className="mt-4 w-full">{action}</div> : null}
    </div>
  );
}

export interface ErrorStateProps {
  /** One sentence naming the fix. */
  sentence: string;
  /** One button. */
  action: ReactNode;
}

/** Error: one sentence naming the fix, one button. */
export function ErrorState({ sentence, action }: ErrorStateProps) {
  return (
    <div className="py-section">
      <p role="alert" className="type-body m-0 text-text">
        {sentence}
      </p>
      <div className="mt-4">{action}</div>
    </div>
  );
}
