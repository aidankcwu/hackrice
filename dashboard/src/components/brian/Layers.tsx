import { T, fmtH, tone } from "@/lib/tokens";
import type { DashboardData, EngineFactor, IconName } from "@/lib/score/types";
import { factorProvenance, measuredOnPage } from "@/lib/score/provenance";
import type { Provenance } from "@/lib/score/provenance";
import { Icon } from "./icons";
import { doseNote } from "@/lib/score/shape";
import { H2, Panel, ProvenanceChip, Track } from "./Panel";

/*
  By layer — screens.md §1.7. Nine rows, in the page's own order: the five
  layers only the glasses can see first (Clock, Light, People, Outside, Mind),
  then the four a wearable already knew (Body, Sleep, Fuel, Recovery).

  The engine groups its 20 factors into eight layers of its own, and "Light &
  clock" is one of them. The wearer's clock and the wearer's light are two
  different instruments on this page — one predicts melatonin onset, the other
  counts bright minutes — so the rows are built from the factor keys rather than
  from the engine's layer names. Every engine factor appears in exactly one row:
  the `LAYER_FACTORS` table below is exhaustive, and `unmappedFactors` surfaces
  anything the engine grows later instead of dropping it silently.
*/

interface LayerSpec {
  name: string;
  icon: IconName;
  /** Engine factor keys that make up this row, most important first. */
  keys: readonly string[];
  /** The engine layer whose 0–100 score this row shows. */
  engineLayer: string;
}

/**
 * The nine rows and the engine factors behind each. Clock and Light both draw on
 * the engine's "Light & clock" score because the engine scores them together —
 * the split here is which factors each row *reports*, not a second scale.
 */
export const LAYER_SPECS: readonly LayerSpec[] = [
  { name: "Clock", icon: "clock", keys: ["sri", "night_light_lux"], engineLayer: "Light & clock" },
  { name: "Light", icon: "sun", keys: ["day_light_min"], engineLayer: "Light & clock" },
  { name: "People", icon: "users", keys: ["social_index", "purpose"], engineLayer: "Social" },
  { name: "Outside", icon: "trees", keys: ["nature_min_wk", "pm25", "noise_night_db"], engineLayer: "Environment" },
  { name: "Mind", icon: "brain", keys: ["rt_z"], engineLayer: "Cognition" },
  {
    name: "Body",
    icon: "footprints",
    keys: ["steps", "vilpa_min", "resistance_min_wk", "fitness_pct", "gait_speed"],
    engineLayer: "Movement",
  },
  { name: "Sleep", icon: "moon", keys: ["sleep_hours"], engineLayer: "Sleep" },
  { name: "Fuel", icon: "utensils", keys: ["med_adherence", "alcohol_drinks", "smoker"], engineLayer: "Diet & substances" },
  { name: "Recovery", icon: "flame", keys: ["sauna_wk", "recovery_ratio"], engineLayer: "Recovery" },
];

const MAPPED_KEYS: ReadonlySet<string> = new Set(LAYER_SPECS.flatMap((s) => s.keys));

/**
 * Factor keys the engine emitted that no row claims. The engine gains factors
 * between releases; a key that lands here is a display gap, and the panel names
 * it rather than quietly leaving its hours off the nine rows.
 */
export const unmappedFactors = (factors: EngineFactor[]): EngineFactor[] =>
  factors.filter((f) => !MAPPED_KEYS.has(f.key));

export interface LayerViewRow {
  name: string;
  icon: IconName;
  score: number;
  hours: number;
  note: string;
  measured: number;
  total: number;
  provenance: Provenance;
}

/**
 * One row per spec. `hours` is the plain sum of the row's own factor hours —
 * the engine's within-layer discount is applied per *engine* layer, so summing
 * a re-cut row's discounted hours is the only total that still adds up to what
 * the factors actually earned.
 */
export function layerViewRows(d: DashboardData): LayerViewRow[] {
  const byKey = new Map(d.factors.map((f) => [f.key, f]));
  return LAYER_SPECS.map((spec) => {
    const factors = spec.keys.map((k) => byKey.get(k)).filter((f): f is EngineFactor => f !== undefined);
    // A glasses value on a day the glasses filed no episode is the engine's
    // default zero, not a sighting: it is unmeasured here, as on the tiles.
    const measured = factors.filter((f) => measuredOnPage(f.key, f.measured, d.source));
    // The row's chip names the stream behind its most important measured factor;
    // with nothing measured it is Imputed, which is what the score counted.
    // `factorProvenance` is the tiles' derivation too, so a row and a tile
    // cannot name different streams for one factor.
    const lead = measured[0];
    return {
      name: spec.name,
      icon: spec.icon,
      score: Math.round(d.layers.find((l) => l.name === spec.engineLayer)?.score ?? 0),
      hours: Number(measured.reduce((sum, f) => sum + f.hours, 0).toFixed(2)),
      note: measured.length > 0 ? doseNote(measured) : "Unmeasured today — scored at the population average, earns nothing.",
      measured: measured.length,
      total: factors.length,
      provenance: lead === undefined ? "imputed" : factorProvenance(lead.key, true, d.source),
    };
  });
}

export function Layers({ d }: { d: DashboardData }) {
  const rows = layerViewRows(d);
  const unmapped = unmappedFactors(d.factors);

  return (
    <Panel id="layers" labelledBy="layers-title">
      <H2 id="layers-title" sub="What each layer earned or cost today.">
        By layer
      </H2>
      {d.factors.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          Put the glasses on. Bryan starts counting light, people, and air the moment the camera is up.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {rows.map((row) => (
            <li
              key={row.name}
              className="tile grid grid-cols-12 items-center gap-x-3 gap-y-2 px-3 py-3"
              style={{ background: T.bg, borderRadius: 16 }}
            >
              {/* Phones: name + hours on one line, track + score below. md and
                  up: the single row the spec draws. */}
              <div className="order-1 col-span-8 flex min-w-0 items-center gap-3 md:col-span-5">
                <span
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                  style={{ background: T.surface }}
                >
                  <Icon name={row.icon} color={row.name === "Clock" ? T.clock : T.ink} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-bold" style={{ color: T.ink, fontSize: 16 }}>
                    {row.name}
                  </div>
                  <div className="truncate text-sm" style={{ color: T.muted }}>
                    {row.note}
                  </div>
                </div>
              </div>
              <div className="order-3 col-span-7 md:order-2 md:col-span-3">
                <Track
                  value={row.score}
                  color={row.measured === 0 ? T.surface2 : row.score < 40 ? T.cost : T.ink}
                  label={`${row.name} score`}
                />
              </div>
              <div
                className="tnum order-4 col-span-2 text-right font-bold md:order-3 md:col-span-1"
                style={{ color: row.measured === 0 ? T.muted : T.ink, fontSize: 16 }}
              >
                {row.measured === 0 ? "—" : row.score}
              </div>
              <div
                className="tnum order-2 col-span-4 text-right font-bold whitespace-nowrap md:order-4 md:col-span-1"
                style={{ color: tone(row.hours), fontSize: 16 }}
              >
                {Math.abs(row.hours) < 0.05 ? "—" : fmtH(row.hours)}
              </div>
              <div className="order-5 col-span-3 flex justify-end md:col-span-2">
                <ProvenanceChip source={row.provenance} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {unmapped.length > 0 && (
        <p className="m-0 mt-4 text-sm" style={{ color: T.muted }}>
          Not yet shown in a layer: {unmapped.map((f) => f.label).join(", ")}.
        </p>
      )}
    </Panel>
  );
}
