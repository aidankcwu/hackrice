"""Validate a JSONL file of decider examples against SCHEMA.md.

    python validate.py FILE [--state-only]

Prints `OK n=<count>` and exits 0, or the first 5 errors (with line numbers) and exits 1.
Stdlib only; the tag enums are read from src/longevity/ai_fields.py (itself stdlib only).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from longevity.ai_fields import ACTIVITY, BOOL_FIELDS, SCENE  # noqa: E402

EPISODE_KINDS = {"meal", "food_sighting", "conversation", "outdoor_block", "screen_block",
                 "gym_session", "sauna_session", "caffeine_sighting", "alcohol_sighting",
                 "medication_sighting"}
ACTIONS = ["annotate", "log_insight", "remember", "watch", "speak", "ask", "act", "look"]
TOPICS = {"caffeine", "food", "alcohol", "screen", "people", "outdoors", "medication",
          "sleep", "movement", "other"}
URGENCY = {"can wait", "soon", "now"}
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
STATE_KEYS = ["trigger", "recent", "episodes", "today", "persona", "trends", "clock"]
FRESH_KEYS = {"age_s", "true", "scene", "activity", "caption", "objects", "hot"}
STALE_KEYS = {"age_s", "true", "no_ai"}
CAPTION_MAX, OBJECT_MAX, SUMMARY_MAX, PERSONA_MAX, TRENDS_MAX = 120, 40, 160, 1200, 1200
MAX_TICKS, MAX_TODAY, STATE_CHARS = 6, 12, 12000


class Bad(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise Bad(msg)


def keys(obj, expected, where):
    need(isinstance(obj, dict), f"{where}: not an object")
    missing, extra = set(expected) - set(obj), set(obj) - set(expected)
    need(not missing and not extra,
         f"{where}: missing {sorted(missing)} extra {sorted(extra)}")


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def string(v, where, cap=None):
    need(isinstance(v, str), f"{where}: not a string")
    need(cap is None or len(v) <= cap, f"{where}: {len(v)} chars > {cap}")


def strings(v, where, cap):
    need(isinstance(v, list), f"{where}: not a list")
    for i, s in enumerate(v):
        string(s, f"{where}[{i}]", cap)


def check_state(s):
    keys(s, STATE_KEYS, "state")
    keys(s["trigger"], ["name", "reason"], "state.trigger")
    string(s["trigger"]["name"], "trigger.name")
    need(re.fullmatch(r"[a-z][a-z0-9_]*", s["trigger"]["name"]), "trigger.name: not snake_case")
    string(s["trigger"]["reason"], "trigger.reason")

    rec = s["recent"]
    need(isinstance(rec, list) and 1 <= len(rec) <= MAX_TICKS, f"recent: need a list of 1-{MAX_TICKS}")
    prev = -1.0
    for i, t in enumerate(rec):
        w = f"recent[{i}]"
        need(isinstance(t, dict), f"{w}: not an object")
        if "no_ai" in t:
            keys(t, STALE_KEYS, w)
            need(t["no_ai"] is True and t["true"] == [], f"{w}: stale tick needs no_ai true and true []")
        else:
            keys(t, FRESH_KEYS, w)
            need(isinstance(t["true"], list), f"{w}.true: not a list")
            unknown = [x for x in t["true"] if x not in BOOL_FIELDS]
            need(not unknown, f"{w}.true: unknown fields {unknown}")
            need(t["true"] == [f for f in BOOL_FIELDS if f in t["true"]],
                 f"{w}.true: duplicates or not in BOOL_FIELDS order")
            need(t["scene"] is None or t["scene"] in SCENE, f"{w}.scene: {t['scene']!r} not in SCENE")
            need(t["activity"] is None or t["activity"] in ACTIVITY,
                 f"{w}.activity: {t['activity']!r} not in ACTIVITY")
            string(t["caption"], f"{w}.caption", CAPTION_MAX)
            strings(t["objects"], f"{w}.objects", OBJECT_MAX)
            strings(t["hot"], f"{w}.hot", OBJECT_MAX)
        need(num(t["age_s"]) and t["age_s"] >= 0, f"{w}.age_s: not a number >= 0")
        need(t["age_s"] >= prev, f"{w}.age_s: recent not newest-first")
        prev = t["age_s"]

    need(isinstance(s["episodes"], list), "episodes: not a list")
    for i, e in enumerate(s["episodes"]):
        w = f"episodes[{i}]"
        keys(e, ["kind", "minutes_open", "label"], w)
        need(e["kind"] in EPISODE_KINDS, f"{w}.kind: {e['kind']!r} not an EpisodeKind")
        need(num(e["minutes_open"]) and e["minutes_open"] >= 0, f"{w}.minutes_open: not a number >= 0")
        string(e["label"], f"{w}.label", OBJECT_MAX)

    strings(s["today"], "today", SUMMARY_MAX)
    need(len(s["today"]) <= MAX_TODAY, f"today: {len(s['today'])} lines > {MAX_TODAY}")
    string(s["persona"], "persona", PERSONA_MAX)
    string(s["trends"], "trends", TRENDS_MAX)

    keys(s["clock"], ["local", "weekday"], "clock")
    local = s["clock"]["local"]
    need(isinstance(local, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", local),
         f"clock.local: {local!r} not HH:MM")
    need(s["clock"]["weekday"] in WEEKDAYS, f"clock.weekday: {s['clock']['weekday']!r}")
    need(len(json.dumps(s)) <= STATE_CHARS, f"state: over {STATE_CHARS} JSON chars")


def check_labels(lab):
    keys(lab, ACTIONS + ["topic", "urgency"], "labels")
    for a in ACTIONS:
        need(isinstance(lab[a], bool), f"labels.{a}: not a boolean")
    need(lab["topic"] in TOPICS, f"labels.topic: {lab['topic']!r}")
    need(lab["urgency"] in URGENCY, f"labels.urgency: {lab['urgency']!r}")
    quiet = not (lab["speak"] or lab["ask"] or lab["act"])
    need(quiet == (lab["urgency"] == "can wait"),
         "labels.urgency: 'can wait' iff speak, ask and act are all false (RULEBOOK §10)")


def check_line(row, state_only, ids):
    need(isinstance(row, dict), "line is not an object")
    need(isinstance(row.get("id"), str) and row["id"], "id: missing or empty")
    need(row["id"] not in ids, f"id: duplicate {row['id']!r}")
    ids.add(row["id"])
    if state_only:
        extra = set(row) - {"id", "state", "real_decision", "source", "labels", "rationale"}
        need("state" in row and not extra, f"top level: missing state or extra {sorted(extra)}")
    else:
        allowed = {"id", "source", "state", "labels", "rationale", "real_decision"}
        missing = {"id", "source", "state", "labels", "rationale"} - set(row)
        extra = set(row) - allowed
        need(not missing and not extra, f"top level: missing {sorted(missing)} extra {sorted(extra)}")
        need(row["source"] in ("real", "synth"), f"source: {row['source']!r}")
        need("real_decision" not in row or row["source"] == "real", "real_decision: only for source real")
        need(isinstance(row["rationale"], str) and 0 < len(row["rationale"]) <= 300,
             "rationale: need 1-300 chars")
        check_labels(row["labels"])
    check_state(row["state"])


def main(argv):
    args = [a for a in argv if a != "--state-only"]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    state_only = "--state-only" in argv
    errors, ids, n = [], set(), 0
    with open(args[0], encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            n += 1
            try:
                check_line(json.loads(line), state_only, ids)
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: bad JSON: {exc}")
            except Bad as exc:
                errors.append(f"line {lineno}: {exc}")
            except (KeyError, TypeError) as exc:
                errors.append(f"line {lineno}: malformed: {exc!r}")
    if errors:
        print(f"FAIL {len(errors)} of {n} lines")
        print("\n".join(errors[:5]))
        return 1
    if n == 0:
        print("FAIL empty file")
        return 1
    print(f"OK n={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
