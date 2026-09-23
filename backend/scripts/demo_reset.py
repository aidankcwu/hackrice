#!/usr/bin/env python3
"""Clear the short-term memory so a demo starts from a blank day.

    uv run python scripts/demo_reset.py            # show what would be cleared
    uv run python scripts/demo_reset.py --yes      # clear it
    uv run python scripts/demo_reset.py --yes --all # clear the measured day too

Why this exists: the voice agent is told what it already said, asked and
settled today, and it will not reopen a settled topic. That is right on an
ordinary day and wrong ten minutes before judges arrive -- rehearse the coffee
question once and the real run skips it, because as far as the system is
concerned it already asked you and you already answered.

Cleared: conversations, questions, today's memory lines, the lines `remember`
learned, and the sessions and log entries (so the Logs list shows only the
demo). Kept: ticks, episodes, decisions, biometrics, the persona override, and
every wearable row -- the day's measured history, which the scores are built
from and which nothing re-asks.

Safe to run with the backend up: these tables are only read at the start of a
wake-up, and the running agent holds no conversation between them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import get_settings  # noqa: E402
from pipeline.db import Database  # noqa: E402

#: Short-term memory only. Anything measured stays.
TABLES = ("conversations", "pending_questions", "today_summary",
          "profile_lines", "recaps", "sessions")
ALL_TABLES = TABLES + ("ticks", "episodes", "decisions", "escalated_frames")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="actually clear it")
    ap.add_argument("--all", action="store_true",
                    help="also clear ticks, episodes, decisions, evidence and live biometrics")
    ap.add_argument("--db", default=None, help="database path (default: settings)")
    args = ap.parse_args()

    path = Path(args.db) if args.db else get_settings().db_path
    db = Database(path).connect()
    try:
        tables = ALL_TABLES if args.all else TABLES
        counts = {}
        for table in tables:
            try:
                counts[table] = db.conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()["c"]
            except Exception:
                counts[table] = None  # table not in this schema yet
        if args.all:
            try:
                counts["biometric_series (live)"] = db.conn.execute(
                    "SELECT COUNT(*) AS c FROM biometric_series WHERE origin = 'live'"
                ).fetchone()["c"]
            except Exception:
                counts["biometric_series (live)"] = None
        width = max(len(t) for t in counts)
        for table, n in counts.items():
            print(f"  {table:<{width}}  {'-' if n is None else n}")
        if not args.yes:
            print("\nnothing cleared. re-run with --yes")
            return 0
        cleared = 0
        with db._lock:
            for table, n in counts.items():
                if n:
                    if table == "biometric_series (live)":
                        db.conn.execute("DELETE FROM biometric_series WHERE origin = 'live'")
                    else:
                        db.conn.execute(f"DELETE FROM {table}")
                    cleared += n
            db.conn.commit()
        kept = "seeded biometrics and persona are untouched" if args.all else (
            "ticks, episodes, decisions, biometrics and the persona are untouched")
        print(f"\ncleared {cleared} rows. {kept}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
