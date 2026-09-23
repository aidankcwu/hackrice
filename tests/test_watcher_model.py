"""US-W03: the watcher model wrapper, exercised through the fake only (no weights, no network)."""

import subprocess
import sys

import numpy as np
import pytest

from longevity import ai_fields
from longevity.watcher_model import (
    FakeWatcherModel,
    bank_from_prompts,
    build_watcher_model,
    scores,
)


def test_embed_is_float32_unit_1d():
    v = FakeWatcherModel(dim=32).embed(b"\xff\xd8frame")
    assert v.shape == (32,)
    assert v.dtype == np.float32
    assert np.linalg.norm(v) == pytest.approx(1.0, abs=1e-5)


def test_embed_is_deterministic_and_byte_sensitive():
    a, b = FakeWatcherModel(), FakeWatcherModel()
    assert np.array_equal(a.embed(b"x"), b.embed(b"x"))
    assert not np.allclose(a.embed(b"x"), a.embed(b"y"))


def test_scripted_override():
    forced = np.zeros(64, np.float32)
    forced[3] = 2.0
    m = FakeWatcherModel(scripted={b"frame": forced})
    v = m.embed(b"frame")
    assert v.dtype == np.float32
    assert v[3] == pytest.approx(1.0) and np.count_nonzero(v) == 1


def test_text_bank_rows_normalised_and_in_order():
    m = FakeWatcherModel()
    bank = m.text_bank(["a cat", "a dog", "a bird"])
    assert bank.shape == (3, 64) and bank.dtype == np.float32
    assert np.allclose(np.linalg.norm(bank, axis=1), 1.0, atol=1e-5)
    assert np.array_equal(bank[1], m.text_bank(["a dog"])[0])
    assert np.array_equal(bank, m.text_bank(["a cat", "a dog", "a bird"]))


def test_bank_from_prompts_covers_every_concept():
    bank, groups, null = bank_from_prompts(FakeWatcherModel())
    pairs = ai_fields.watch_prompt_bank()
    assert bank.shape == (len(pairs), 64)
    assert set(groups) == set(ai_fields.WATCH_PROMPTS)
    assert [pairs[i][1] for i in null] == list(ai_fields.WATCH_NULL_PROMPTS)
    assert [pairs[i][1] for i in groups["food_present"]] == list(ai_fields.WATCH_PROMPTS["food_present"])


def test_scores_in_unit_interval():
    m = FakeWatcherModel()
    bank, groups, null = bank_from_prompts(m)
    for frame in (b"a", b"b", b"c"):
        s = scores(m.embed(frame), bank, groups, null)
        assert set(s) == set(groups)
        assert all(0.0 <= v <= 1.0 for v in s.values())


def _bank_vector(prompt: str) -> np.ndarray:
    return FakeWatcherModel().text_bank([prompt])[0]


def test_frame_on_a_food_prompt_scores_food_high():
    food = ai_fields.WATCH_PROMPTS["food_present"][0]
    m = FakeWatcherModel(scripted={b"lunch": _bank_vector(food)})
    bank, groups, null = bank_from_prompts(m)
    assert scores(m.embed(b"lunch"), bank, groups, null)["food_present"] > 0.9


def test_frame_on_a_null_prompt_scores_everything_low():
    m = FakeWatcherModel(scripted={b"wall": _bank_vector(ai_fields.WATCH_NULL_PROMPTS[1])})
    bank, groups, null = bank_from_prompts(m)
    s = scores(m.embed(b"wall"), bank, groups, null)
    assert all(v < 0.1 for v in s.values()), s


def test_scores_do_not_overflow_at_extreme_temperature():
    m = FakeWatcherModel()
    bank, groups, null = bank_from_prompts(m)
    s = scores(m.embed(b"x"), bank, groups, null, temperature=1e6)
    assert all(np.isfinite(v) and 0.0 <= v <= 1.0 for v in s.values())


def test_build_watcher_model():
    m = build_watcher_model("fake")
    assert m.name == "fake"
    assert m.embed(b"x").shape == (64,)
    with pytest.raises(ValueError):
        build_watcher_model("nope")


def test_importing_the_module_does_not_import_torch():
    code = "import sys, longevity.watcher_model; assert 'torch' not in sys.modules; assert 'open_clip' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_missing_torch_names_the_extra(monkeypatch):
    # None in sys.modules makes the import raise ImportError without touching the real one.
    monkeypatch.setitem(sys.modules, "open_clip", None)
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(RuntimeError, match=r"uv sync --extra watcher"):
        build_watcher_model("mobileclip2-s0")
