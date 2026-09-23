from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "vc_smoke.py"
SPEC = importlib.util.spec_from_file_location("vc_smoke", SCRIPT)
vc_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = vc_smoke
SPEC.loader.exec_module(vc_smoke)


def test_parse_target_derives_ws_https_prefix_and_decodes_token() -> None:
    target = vc_smoke.parse_target(
        "wss://host.example:8443/t/judge-1/ws/glasses?token=a%2Bb%20c"
    )
    assert target.ws_url.endswith("token=a%2Bb%20c")
    assert target.api_base == "https://host.example:8443/t/judge-1"
    assert target.prefix == "/t/judge-1"
    assert target.token == "a+b c"


@pytest.mark.parametrize(
    "url, message",
    [
        ("ws://host/t/a/ws/glasses?token=x", "wss://"),
        ("wss://host/t/a/not-glasses?token=x", "/ws/glasses"),
        ("wss://host/t/a/ws/glasses", "token"),
        ("wss://host/t/a/ws/glasses?token=", "token"),
        ("wss://host/t/a/ws/glasses?token=a&token=b", "token"),
    ],
)
def test_parse_target_rejects_bad_contract_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        vc_smoke.parse_target(url)


def test_header_building_uses_access_token() -> None:
    target = vc_smoke.parse_target("wss://host/t/alice/ws/glasses?token=secret")
    assert vc_smoke.auth_headers(target) == {"X-Access-Token": "secret"}
    assert "secret-wrong" in vc_smoke.wrong_token_url(target)


@pytest.mark.parametrize(
    "after, expected, reason",
    [
        ({"tick_count": 14, "ai_coverage": 0.75}, True, ""),
        ({"tick_count": 10, "ai_coverage": 0.75}, False, "no new ticks"),
        ({"tick_count": 14, "ai_coverage": 0.49}, False, "below 0.50"),
    ],
)
def test_stream_pass_fail_logic(after: dict, expected: bool, reason: str) -> None:
    verdict = vc_smoke.stream_verdict(
        {"tick_count": 10, "ai_coverage": 0.0}, after, 2, 3
    )
    assert verdict.passed is expected
    assert reason in verdict.reason
    assert verdict.decision_produced is True


def test_decision_is_reported_but_not_required_to_pass() -> None:
    verdict = vc_smoke.stream_verdict(
        {"tick_count": 1}, {"tick_count": 2, "ai_coverage": 0.5}, 4, 4
    )
    assert verdict.passed is True
    assert verdict.decision_produced is False
