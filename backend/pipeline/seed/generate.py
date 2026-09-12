"""Load the fixed 7-day dataset into SQLite, and summarise it in prose.

:func:`seed_database` is idempotent -- re-running it on a database that already
has seeded rows for the window is a no-op unless ``force=True``.

:func:`seven_day_summary` produces part 2 of the T1 context envelope (SPEC
§4.2: "7-day summary -- trends and baselines"), which lives in the **stable
prefix** and so must be cheap and change at most daily. The prose is derived
from the rows in the database, not hardcoded: reseed with different fixtures and
the summary follows.
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from ..db import Database, day_key
from ..models import Episode
from .fixtures import DAY_COUNT, days_ending, seed_live_episodes, seed_rows

__all__ = ["seed_database", "seven_day_summary", "main"]

LATE_COFFEE_FROM_HOUR = 16.0
SHORT_SLEEP_BELOW_H = 6.5
HRV_FLAG_BELOW = 0.9


# -- loading -------------------------------------------------------------


def _rekey_episodes_by_day(episodes: list[Episode]) -> list[Episode]:
    """Re-id seeded episodes so the id depends only on the calendar day.

    :func:`seed_live_episodes` numbers ids sequentially within one call
    (``e_seed_0001``, ...), keyed by the episode's *position in the window*,
    not by calendar day. Two overlapping windows (the normal case when the
    end day advances by one) can therefore hand the same id to two different
    days' episodes -- e.g. whichever day lands last in the window is always
    id-numbered like the previous call's last day. Since ``upsert_episode``
    stores episodes keyed by id via ``INSERT OR REPLACE``, seeding a new day
    under a reused id would silently overwrite an unrelated, already-seeded
    day. Re-keying by day (and by order within that day) makes the id unique
    across days and stable across calls for the same day.
    """

    seen: dict[str, int] = {}
    rekeyed: list[Episode] = []
    for episode in episodes:
        day = day_key(episode.start_t)
        n = seen.get(day, 0)
        seen[day] = n + 1
        rekeyed.append(episode.model_copy(update={"id": f"e_seed_{day}_{n:02d}"}))
    return rekeyed


def seed_database(
    db: Database, end_day: str | None = None, *, force: bool = False
) -> dict:
    """Insert the 7-day seeded rows and the 6 days of historical episodes.

    Idempotent per ``(day, metric)`` pair, not per window: consecutive days'
    windows overlap by six days, so a range is only considered "already
    seeded" when *every* expected pair for the requested window is present.
    Otherwise only the missing days are inserted -- existing days (and the
    values already on them) are left untouched, so the window can advance one
    day at a time without losing yesterday's data or re-writing it under a
    different day-of-window index.

    Returns ``{"end_day", "days", "seeded_rows", "episodes", "skipped"}``.
    """

    end = end_day or day_key(time.time())
    days = days_ending(end)
    existing = db.list_seeded(days[0], days[-1])
    existing_pairs = {(r.day, r.metric) for r in existing}

    all_rows = seed_rows(end)
    if force:
        rows_to_insert = all_rows
    else:
        rows_to_insert = [r for r in all_rows if (r.day, r.metric) not in existing_pairs]

    # Historical episodes cover the 6 days *before* end_day -- end_day itself
    # is "today" and gets its live episodes from the tick stream, not seeding.
    historical_days = days[:-1]
    all_episodes = _rekey_episodes_by_day(seed_live_episodes(end))
    if force:
        episodes_to_insert = all_episodes
    else:
        needing_episodes = {d for d in historical_days if not db.list_episodes(d)}
        episodes_to_insert = [
            e for e in all_episodes if day_key(e.start_t) in needing_episodes
        ]

    if not force and not rows_to_insert and not episodes_to_insert:
        return {
            "end_day": end,
            "days": days,
            "seeded_rows": 0,
            "episodes": 0,
            "skipped": True,
            "existing_rows": len(existing),
        }

    inserted = db.insert_seeded_rows(rows_to_insert)
    for episode in episodes_to_insert:
        db.upsert_episode(episode)
    return {
        "end_day": end,
        "days": days,
        "seeded_rows": inserted,
        "episodes": len(episodes_to_insert),
        "skipped": False,
        "existing_rows": len(existing),
    }


# -- summary -------------------------------------------------------------


def _weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%a")


def _hhmm(hours: float) -> str:
    total = int(round((hours % 24.0) * 60.0))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _local_hour(t: float) -> float:
    from datetime import datetime

    dt = datetime.fromtimestamp(t)
    return dt.hour + dt.minute / 60.0


def seven_day_summary(db: Database, end_day: str) -> str:
    """A 6-10 line plain-text week summary for the T1 stable prefix (SPEC §4.2)."""

    days = days_ending(end_day, DAY_COUNT)
    by_day: dict[str, dict[str, float]] = {d: {} for d in days}
    for row in db.list_seeded(days[0], days[-1]):
        by_day.setdefault(row.day, {})[row.metric] = row.value

    def series(metric: str) -> list[tuple[str, float]]:
        return [(d, by_day[d][metric]) for d in days if metric in by_day.get(d, {})]

    episodes_by_day: dict[str, list[Episode]] = {d: db.list_episodes(d) for d in days}

    lines: list[str] = [
        f"7-day window {days[0]} to {days[-1]} "
        f"({sum(len(v) for v in by_day.values())} seeded rows, "
        f"{sum(len(v) for v in episodes_by_day.values())} live episodes)."
    ]

    # -- sleep, and the nights that went wrong ---------------------------
    sleep = series("sleep_hours")
    short = [(d, v) for d, v in sleep if v < SHORT_SLEEP_BELOW_H]
    if sleep:
        mean_sleep = _mean([v for _, v in sleep]) or 0.0
        if short:
            names = ", ".join(_weekday(d) for d, _ in short)
            lines.append(
                f"Sleep averaged {mean_sleep:.1f} h; {len(short)} night(s) under "
                f"{SHORT_SLEEP_BELOW_H:.1f} h ({names}), "
                f"{min(v for _, v in short):.1f}-{max(v for _, v in short):.1f} h."
            )
        else:
            lines.append(f"Sleep averaged {mean_sleep:.1f} h, no short nights.")

    # -- the pattern worth finding ---------------------------------------
    late_coffee_days = [
        d
        for d in days
        if any(
            e.kind == "caffeine_sighting" and _local_hour(e.start_t) >= LATE_COFFEE_FROM_HOUR
            for e in episodes_by_day.get(d, [])
        )
    ]
    short_days = [d for d, _ in short]
    overlap = [d for d in short_days if d in late_coffee_days]
    if overlap:
        beds = [by_day[d].get("bed_time") for d in overlap if "bed_time" in by_day.get(d, {})]
        normal_beds = [
            by_day[d]["bed_time"]
            for d in days
            if d not in overlap and "bed_time" in by_day.get(d, {})
        ]
        bed_txt = ""
        if beds:
            bed_txt = f" Bedtime slipped to {_hhmm(max(beds))}"
            if normal_beds:
                bed_txt += f" from {_hhmm(_mean(normal_beds) or 0.0)}"
            bed_txt += "."
        lines.append(
            f"{len(overlap)} of those {len(short_days)} short night(s) followed a coffee "
            f"after {_hhmm(LATE_COFFEE_FROM_HOUR)} the same afternoon "
            f"({', '.join(_weekday(d) for d in overlap)}).{bed_txt}"
        )
    elif late_coffee_days:
        lines.append(
            f"Caffeine after {_hhmm(LATE_COFFEE_FROM_HOUR)} on "
            f"{', '.join(_weekday(d) for d in late_coffee_days)}, with no sleep cost visible."
        )

    # -- HRV -------------------------------------------------------------
    hrv = series("hrv_rmssd_ratio")
    if hrv:
        flagged = [(d, v) for d, v in hrv if v < HRV_FLAG_BELOW]
        rest = [v for d, v in hrv if v >= HRV_FLAG_BELOW]
        if flagged and rest:
            lines.append(
                f"HRV recovery (7d/60d ln RMSSD) fell to "
                f"{_mean([v for _, v in flagged]):.2f} on "
                f"{', '.join(_weekday(d) for d, _ in flagged)} vs "
                f"{_mean(rest):.2f} otherwise; below-baseline flag at {HRV_FLAG_BELOW:.2f}."
            )
        else:
            lines.append(
                f"HRV recovery averaged {_mean([v for _, v in hrv]):.2f} of baseline."
            )

    # -- regularity ------------------------------------------------------
    sri = series("sleep_regularity_sri")
    if sri:
        lines.append(
            f"Sleep regularity SRI averaged {_mean([v for _, v in sri]):.0f} "
            f"(low {min(v for _, v in sri):.0f}, high {max(v for _, v in sri):.0f}); target 80."
        )

    # -- movement --------------------------------------------------------
    steps = series("steps")
    if steps:
        lines.append(
            f"Steps trending down {steps[0][1] / 1000:.1f}k -> {steps[-1][1] / 1000:.1f}k "
            f"across the week, averaging {(_mean([v for _, v in steps]) or 0) / 1000:.1f}k "
            f"against a 7,000/day target."
        )
    vilpa = series("vilpa_minutes")
    if vilpa:
        lines.append(
            f"VILPA averaged {_mean([v for _, v in vilpa]):.1f} min/day against a 3-4 min target."
        )

    # -- live side -------------------------------------------------------
    all_episodes = [e for d in days for e in episodes_by_day.get(d, [])]
    nature_min = sum(e.duration_s for e in all_episodes if e.kind == "outdoor_block") / 60.0
    screen_days = [
        sum(e.duration_s for e in episodes_by_day.get(d, []) if e.kind == "screen_block") / 3600.0
        for d in days
        if any(e.kind == "screen_block" for e in episodes_by_day.get(d, []))
    ]
    meals = sum(1 for e in all_episodes if e.kind == "meal")
    convos = sum(1 for e in all_episodes if e.kind == "conversation")
    screen_txt = (
        f"screen {_mean(screen_days):.1f} h/day" if screen_days else "no screen blocks logged"
    )
    lines.append(
        f"Live: nature {nature_min:.0f} min/week vs a 120 min target, {screen_txt}, "
        f"{convos} conversation episodes and {meals} meals observed."
    )

    # -- the rest of the seeded panel ------------------------------------
    noise = series("night_noise_db")
    light = series("daytime_light_minutes")
    purpose = series("purpose_score")
    tail = []
    if noise:
        tail.append(f"night noise {_mean([v for _, v in noise]):.0f} dB (<45 target)")
    if light:
        tail.append(f"daytime light {_mean([v for _, v in light]):.0f} min/day (30 target)")
    if purpose:
        tail.append(f"purpose {_mean([v for _, v in purpose]):.1f}/5")
    if tail:
        lines.append("Baselines: " + ", ".join(tail) + ".")

    return "\n".join(lines)


# -- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.seed.generate",
        description="Seed the fixed 7-day dataset and print its summary.",
    )
    parser.add_argument("--db", default="data/pipeline.db", help="SQLite path")
    parser.add_argument("--end-day", default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--force", action="store_true", help="reseed even if rows exist")
    args = parser.parse_args(argv)

    db = Database(args.db).connect().init_schema()
    try:
        result = seed_database(db, args.end_day, force=args.force)
        status = "already seeded" if result["skipped"] else "seeded"
        print(
            f"[{status}] {result['end_day']}: {result['seeded_rows']} rows, "
            f"{result['episodes']} episodes -> {args.db}"
        )
        print()
        print(seven_day_summary(db, result["end_day"]))
    finally:
        db.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
