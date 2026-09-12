"""Score a list of requests in one interpreter.

stdin:  JSON array of run_from_json requests
stdout: JSON array of payloads, same order

Exists so the dashboard's seven-day chart (one engine run per day) does not
pay the numpy import once per day. Imports brian_score unchanged.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brian_score import run_from_json  # noqa: E402

if __name__ == "__main__":
    requests = json.load(sys.stdin)
    if not isinstance(requests, list):
        raise SystemExit("brian_batch.py expects a JSON array of requests on stdin")
    print(json.dumps([run_from_json(req) for req in requests], default=float))
