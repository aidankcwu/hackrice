/** Shown while the engine subprocess scores the day (about a second). Grey panels only — no placeholder numbers. */
export default function FactorsLoading() {
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <div className="h-14 bg-header" aria-hidden="true" />
      <main className="mx-auto w-full max-w-6xl px-6 py-8" aria-busy="true">
        <p className="text-muted">Scoring today with the engine…</p>
        <div className="mt-6 space-y-6" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-40 rounded-panel bg-surface" />
          ))}
        </div>
      </main>
    </div>
  );
}
