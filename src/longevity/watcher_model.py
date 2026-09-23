"""The watcher's model: JPEG in, embedding and concept scores out (docs/PERCEPTION.md, "Watcher").

One class per backend behind the `WatcherModel` protocol, in the same shape as
`vlm.VLMClient` / `vlm.FakeClient`:

- `FakeWatcherModel` -- deterministic hash embeddings, numpy only. Tests and CI use it
  so nothing ever downloads weights.
- `MobileCLIPModel` -- Apple's MobileCLIP2 (S0 recommended, S2 the upgrade) through
  open_clip. torch and open_clip are imported inside ``__init__`` only, so importing
  this module never pulls in torch; they come from the optional extra
  (``uv sync --extra watcher``).

Loading order in `MobileCLIPModel`:

1. open_clip's built-in pretrained tag (``create_model_and_transforms("MobileCLIP2-S0",
   pretrained="dfndr2b")``), which resolves to the ``timm/MobileCLIP2-S0-OpenCLIP`` repo
   on the Hugging Face hub.
2. If the locked open_clip does not know that tag, ``hf_hub_download`` Apple's own
   checkpoint (``apple/MobileCLIP2-S0``, ``mobileclip2_s0.pt``) into the default HF cache
   and load it with ``pretrained=<path>``.

On this machine (macOS, Apple silicon, open_clip_torch 3.3.0, torch 2.14.0) path 1
worked; open_clip 3.3 lists MobileCLIP2-S0/S2 with the ``dfndr2b`` tag. Path 2 was
forced once and also loads: its text bank matches path 1's (min row cosine 0.9999998).
Device was ``mps``, about 7 ms per 512 px frame. The ``mobileclip`` package is not
installed, so reparameterisation uses timm's equivalent ``reparameterize_model``.

`scores` is pure numpy: per concept, the max cosine over its prompts, softmaxed
against the max over the null prompts. A CLIP score is a similarity, not a
calibrated probability; thresholds are set offline (PERCEPTION.md, "Calibration").
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import numpy as np

from longevity import ai_fields

log = logging.getLogger(__name__)

_INSTALL_HINT = "the watcher model needs torch and open_clip: run `uv sync --extra watcher`"

# Variant -> (open_clip model name, open_clip pretrained tag, Apple HF repo, checkpoint file).
_VARIANTS: dict[str, tuple[str, str, str, str]] = {
    "mobileclip2-s0": ("MobileCLIP2-S0", "dfndr2b", "apple/MobileCLIP2-S0", "mobileclip2_s0.pt"),
    "mobileclip2-s2": ("MobileCLIP2-S2", "dfndr2b", "apple/MobileCLIP2-S2", "mobileclip2_s2.pt"),
}

_reparam_missing_logged = False


class WatcherModel(Protocol):
    """Anything that turns a JPEG into an L2-normalised embedding and prompts into a bank."""

    name: str

    def embed(self, jpeg: bytes) -> np.ndarray: ...

    def text_bank(self, prompts: Sequence[str]) -> np.ndarray: ...


def _normalise(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return (v / np.where(n == 0, 1, n)).astype(np.float32)


def _hash_vector(data: bytes, dim: int) -> np.ndarray:
    seed = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "little")
    return _normalise(np.random.default_rng(seed).standard_normal(dim))


class FakeWatcherModel:
    """Deterministic stand-in: the embedding is a pure function of the bytes.

    `scripted` maps exact JPEG bytes to a forced embedding (normalised on the way out),
    which is how a test puts a frame right on top of a prompt's text vector.
    """

    name = "fake"

    def __init__(self, dim: int = 64, scripted: dict[bytes, np.ndarray] | None = None) -> None:
        self.dim = dim
        self._scripted = dict(scripted or {})
        self.calls = 0

    def embed(self, jpeg: bytes) -> np.ndarray:
        self.calls += 1
        if jpeg in self._scripted:
            return _normalise(self._scripted[jpeg])
        return _hash_vector(b"img:" + jpeg, self.dim)

    def text_bank(self, prompts: Sequence[str]) -> np.ndarray:
        rows = [_hash_vector(b"txt:" + p.encode(), self.dim) for p in prompts]
        return np.stack(rows).astype(np.float32) if rows else np.zeros((0, self.dim), np.float32)


class MobileCLIPModel:
    """MobileCLIP2 through open_clip, on MPS when available, else CPU."""

    def __init__(self, name: str = "mobileclip2-s0", device: str | None = None) -> None:
        if name not in _VARIANTS:
            raise ValueError(f"unknown MobileCLIP variant {name!r}; expected one of {sorted(_VARIANTS)}")
        try:
            import open_clip
            import torch
        except ImportError as e:
            raise RuntimeError(f"{_INSTALL_HINT} ({e})") from e

        self.name = name
        self._torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        oc_name, tag, repo, filename = _VARIANTS[name]

        try:
            model, _, preprocess = open_clip.create_model_and_transforms(oc_name, pretrained=tag)
            self.load_path = f"open_clip:{oc_name}/{tag}"
        except (RuntimeError, ValueError) as e:
            log.info("open_clip has no %s/%s (%s); downloading %s/%s", oc_name, tag, e, repo, filename)
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(repo_id=repo, filename=filename)
            # MobileCLIP2 checkpoints expect unnormalised [0, 1] pixels.
            model, _, preprocess = open_clip.create_model_and_transforms(
                oc_name, pretrained=path, image_mean=(0.0, 0.0, 0.0), image_std=(1.0, 1.0, 1.0)
            )
            self.load_path = f"hf_hub_download:{repo}/{filename}"

        model = _reparameterize(model)
        model.eval()
        self._model = model.to(self.device)
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(oc_name)

        from PIL import Image

        blank = io.BytesIO()
        Image.new("RGB", (64, 64), (0, 0, 0)).save(blank, "JPEG")
        self.embed(blank.getvalue())

    def embed(self, jpeg: bytes) -> np.ndarray:
        from PIL import Image

        torch = self._torch
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        x = self._preprocess(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            feat = self._model.encode_image(x)
        return _normalise(feat[0].float().cpu().numpy())

    def text_bank(self, prompts: Sequence[str]) -> np.ndarray:
        torch = self._torch
        tokens = self._tokenizer(list(prompts)).to(self.device)
        with torch.no_grad():
            feat = self._model.encode_text(tokens)
        return _normalise(feat.float().cpu().numpy())


def _reparameterize(model: Any) -> Any:
    """Fold MobileOne/FastViT train-time branches into single convs, if a helper exists."""
    global _reparam_missing_logged
    try:
        from mobileclip.modules.common.mobileone import reparameterize_model
    except ImportError:
        try:
            from timm.utils import reparameterize_model  # ships with open_clip's timm dep
        except ImportError:
            if not _reparam_missing_logged:
                log.info("no reparameterize_model available; running the unfused model")
                _reparam_missing_logged = True
            return model
    return reparameterize_model(model)


def build_watcher_model(kind: str) -> WatcherModel:
    """"fake" or a MobileCLIP2 variant ("mobileclip2-s0", "mobileclip2-s2")."""
    if kind == "fake":
        return FakeWatcherModel()
    if kind in _VARIANTS:
        return MobileCLIPModel(kind)
    raise ValueError(f"unknown watcher model {kind!r}; expected 'fake' or one of {sorted(_VARIANTS)}")


def scores(
    embedding: np.ndarray,
    bank: np.ndarray,
    groups: Mapping[str, Sequence[int]],
    null_group: Sequence[int],
    temperature: float = 100.0,
) -> dict[str, float]:
    """Per concept: exp(T*s_c) / (exp(T*s_c) + exp(T*s_null)), with s = max cosine over rows."""
    sims = bank @ np.asarray(embedding, dtype=np.float32)
    s_null = float(sims[list(null_group)].max())
    out: dict[str, float] = {}
    for concept, rows in groups.items():
        d = temperature * (float(sims[list(rows)].max()) - s_null)
        # The two-way softmax is a logistic in the difference; tanh form never overflows.
        out[concept] = float(0.5 * (1.0 + np.tanh(0.5 * d)))
    return out


def bank_from_prompts(model: WatcherModel) -> tuple[np.ndarray, dict[str, list[int]], list[int]]:
    """Encode `ai_fields.watch_prompt_bank()` once: (bank, rows per concept, null rows)."""
    pairs = ai_fields.watch_prompt_bank()
    bank = model.text_bank([p for _, p in pairs])
    groups: dict[str, list[int]] = {}
    null: list[int] = []
    for i, (concept, _) in enumerate(pairs):
        if concept == "_null":
            null.append(i)
        else:
            groups.setdefault(concept, []).append(i)
    return bank, groups, null
