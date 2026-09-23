"""tools/probe_watcher.py end to end on the fake model (no weights, no Gemini)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

_PATH = Path(__file__).resolve().parent.parent / "tools" / "probe_watcher.py"
_spec = importlib.util.spec_from_file_location("probe_watcher", _PATH)
probe_watcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_watcher)


def _corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rng = np.random.default_rng(0)
    rows = []
    for i in range(6):
        name = f"frame_{1757700842000 + i * 1500}.jpg"
        buf = io.BytesIO()
        Image.fromarray(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)).save(buf, "JPEG", quality=70)
        (corpus / name).write_bytes(buf.getvalue())
        rows.append({"file": name, "ai": {"food_present": i < 3}})
    labels = tmp_path / "labels.jsonl"
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return corpus, labels


def test_fake_model_writes_thresholds(tmp_path, capsys):
    corpus, labels = _corpus(tmp_path)
    out = tmp_path / "thresholds.json"
    rc = probe_watcher.main(
        ["--corpus", str(corpus), "--model", "fake", "--labels", str(labels), "--out", str(out)]
    )
    assert rc == 0
    with out.open() as fh:
        data = json.load(fh)
    assert data["food_present"]["enter"] > data["food_present"]["exit"]
    assert data["_meta"]["model"] == "fake" and data["_meta"]["frames"] == 6
    # Every other concept has no positives, so it stays at the backend defaults.
    assert set(data) == {"food_present", "_meta"}
    stdout = capsys.readouterr().out
    assert "concept" in stdout and "false_wake" in stdout
    assert "insufficient positives" in stdout


def test_corpus_from_env(tmp_path, monkeypatch, capsys):
    corpus, labels = _corpus(tmp_path)
    monkeypatch.setenv("CORPUS_DIR", str(corpus))
    assert probe_watcher.main(["--model", "fake", "--labels", str(labels)]) == 0


def test_no_corpus_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("CORPUS_DIR", raising=False)
    assert probe_watcher.main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_sweep_prefers_lowest_false_wake_at_target_recall():
    enter, recall, fw, note = probe_watcher.sweep([0.9, 0.8, 0.7], [0.1, 0.75], 0.9)
    assert (enter, recall, fw, note) == (0.7, 1.0, 0.5, "")
    enter, recall, fw, note = probe_watcher.sweep([0.01, 0.02, 0.9], [0.5], 0.9)
    assert note == "low-recall" and recall < 0.9
