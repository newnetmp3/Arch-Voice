"""Synthetic impulse-response generator for the convolution reverbs.

Real recorded IRs would bloat the package and carry licensing baggage, so
we synthesise them: exponentially decaying noise with frequency-dependent
damping, a pre-delay and a handful of early reflections. The results are
cached as 16-bit mono WAV files and loaded by PipeWire's builtin
``convolver`` node.
"""

from __future__ import annotations

import array
import math
import os
import random
import wave
from dataclasses import dataclass
from pathlib import Path

RATE = 48000
VERSION = 1  # bump to invalidate cached files when the algorithm changes


@dataclass(frozen=True)
class Space:
    rt60: float          # seconds for the tail to decay by 60 dB
    predelay: float      # seconds before the first reflection
    damping: float       # 0 = bright, 1 = very dark (high-freq absorption)
    early: int           # number of discrete early reflections
    early_spread: float  # seconds over which early reflections are scattered
    density: float = 1.0  # < 1 gives a grainier, sparser tail


SPACES: dict[str, Space] = {
    "room":      Space(rt60=0.45, predelay=0.004, damping=0.45, early=8,  early_spread=0.025),
    "bathroom":  Space(rt60=0.9,  predelay=0.002, damping=0.15, early=14, early_spread=0.015),
    "hall":      Space(rt60=2.0,  predelay=0.020, damping=0.40, early=10, early_spread=0.060),
    "cave":      Space(rt60=3.2,  predelay=0.035, damping=0.65, early=6,  early_spread=0.120, density=0.6),
    "cathedral": Space(rt60=5.0,  predelay=0.045, damping=0.35, early=12, early_spread=0.100),
    "metal":     Space(rt60=1.2,  predelay=0.001, damping=0.05, early=20, early_spread=0.008),
    "tank":      Space(rt60=0.7,  predelay=0.003, damping=0.85, early=10, early_spread=0.020),
}


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "arch-voice" / "ir"


def ir_path(name: str) -> Path:
    if name not in SPACES:
        raise KeyError(f"unknown space {name!r}")
    path = cache_dir() / f"{name}-v{VERSION}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        _write_wav(tmp, generate(SPACES[name], seed=hash_name(name)))
        tmp.replace(path)
    return path


def hash_name(name: str) -> int:
    # Stable across runs (unlike hash()).
    h = 2166136261
    for c in name.encode():
        h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return h


def generate(space: Space, seed: int = 0, rate: int = RATE) -> list[float]:
    rng = random.Random(seed)
    length = int(rate * (space.predelay + space.rt60 * 1.1)) + 1
    out = [0.0] * length
    pre = int(space.predelay * rate)

    # Direct-ish first reflection plus scattered early reflections.
    out[pre] = 0.6
    for _ in range(space.early):
        t = pre + int(rng.uniform(0.0015, space.early_spread) * rate)
        if t < length:
            out[t] += rng.choice((-1, 1)) * rng.uniform(0.15, 0.5)

    # Diffuse tail: decaying noise run through a one-pole low-pass whose
    # coefficient increases over time, so high frequencies die first.
    decay_per_sample = math.log(1000.0) / (space.rt60 * rate)  # -60 dB at rt60
    lp = 0.0
    tail_start = pre + int(space.early_spread * rate * 0.3)
    for i in range(tail_start, length):
        t = i - tail_start
        env = math.exp(-decay_per_sample * t)
        if space.density < 1.0 and rng.random() > space.density:
            noise = 0.0
        else:
            noise = rng.gauss(0.0, 1.0)
        progress = min(1.0, t / (space.rt60 * rate))
        coeff = min(0.97, space.damping * (0.4 + 0.6 * progress))
        lp = coeff * lp + (1.0 - coeff) * noise
        # Fade the tail in over ~10 ms to avoid a click at tail_start.
        fade = min(1.0, t / (0.01 * rate))
        out[i] += lp * env * fade * 0.35

    # Normalise to unit energy so wet level is roughly loudness-neutral.
    energy = math.sqrt(sum(s * s for s in out)) or 1.0
    peak = max(abs(s) for s in out) / energy
    scale = 1.0 / energy
    if peak > 0.99:  # keep within 16-bit range
        scale *= 0.99 / peak
    return [s * scale for s in out]


def _write_wav(path: Path, samples: list[float], rate: int = RATE) -> None:
    data = array.array("h", (max(-32767, min(32767, int(s * 32767))) for s in samples))
    if data.itemsize != 2:
        raise RuntimeError("unexpected short size")
    import sys
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
