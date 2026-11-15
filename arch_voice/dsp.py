"""A tiny DSL for building PipeWire ``filter-chain`` graphs.

Presets are written as ordinary Python functions that wire together
building blocks (pitch shift, filters, ring modulation, reverb...). The
:class:`Graph` collects nodes and links and serialises them into the
``filter.graph`` section of a filter-chain module.

Control values may be :class:`PV` ("parameter value") instances. A PV is a
float that remembers which user-facing preset parameter it was derived
from and how. Whenever a PV lands on a control port, the graph records a
*binding*, which lets the engine update that port live (via ``pw-cli
set-param``) when the user moves a slider, without restarting the chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from . import irgen

# --------------------------------------------------------------------------
# Parameter values that remember where they came from


def _identity(v: float) -> float:
    return v


class PV(float):
    """A float tagged with the preset parameter it derives from."""

    key: str
    fn: Callable[[float], float]

    def __new__(cls, value: float, key: str, fn: Callable[[float], float] = _identity):
        obj = super().__new__(cls, value)
        obj.key = key
        obj.fn = fn
        return obj

    def _derive(self, op: Callable[[float], float]) -> "PV":
        inner = self.fn
        return PV(op(float(self)), self.key, lambda v: op(inner(v)))

    def __add__(self, o):  return self._derive(lambda v: v + float(o))
    def __radd__(self, o): return self._derive(lambda v: float(o) + v)
    def __sub__(self, o):  return self._derive(lambda v: v - float(o))
    def __rsub__(self, o): return self._derive(lambda v: float(o) - v)
    def __mul__(self, o):  return self._derive(lambda v: v * float(o))
    def __rmul__(self, o): return self._derive(lambda v: float(o) * v)
    def __truediv__(self, o):  return self._derive(lambda v: v / float(o))
    def __rtruediv__(self, o): return self._derive(lambda v: float(o) / v)
    def __neg__(self):     return self._derive(lambda v: -v)
    def __pow__(self, o):  return self._derive(lambda v: v ** float(o))

    def map(self, op: Callable[[float], float]) -> "PV":
        return self._derive(op)


@dataclass
class Binding:
    key: str
    node: str
    port: str
    fn: Callable[[float], float]


# --------------------------------------------------------------------------
# Graph


@dataclass
class Node:
    name: str
    type: str
    label: str
    plugin: str | None = None
    control: dict[str, float] = field(default_factory=dict)
    config: dict | None = None

    def __getitem__(self, port: str) -> str:
        return f"{self.name}:{port}"

    def to_spa(self) -> dict:
        d: dict = {"type": self.type, "name": self.name}
        if self.plugin:
            d["plugin"] = self.plugin
        d["label"] = self.label
        if self.config:
            d["config"] = self.config
        if self.control:
            d["control"] = {k: float(v) for k, v in self.control.items()}
        return d


# A "signal" is the output port that carries audio at some point in the chain.
Signal = str


@dataclass
class PitchEngine:
    plugin: str = "ladspa-rubberband"
    label: str = "rubberband-pitchshifter-mono"


class Graph:
    """Collects nodes/links. Every builder method takes and returns a Signal."""

    def __init__(self, pitch_engine: PitchEngine | None = None):
        self.nodes: list[Node] = []
        self.links: list[tuple[str, str]] = []
        self.bindings: list[Binding] = []
        self._counts: dict[str, int] = {}
        self.pitch_engine = pitch_engine or PitchEngine()
        self.entry = self._node("builtin", "copy", name="in")
        self.input: Signal = self.entry["Out"]

    # -- plumbing ----------------------------------------------------------

    def _unique(self, base: str) -> str:
        n = self._counts.get(base, 0)
        self._counts[base] = n + 1
        return base if n == 0 else f"{base}{n + 1}"

    def _node(self, type_: str, label: str, name: str | None = None,
              plugin: str | None = None, control: dict | None = None,
              config: dict | None = None) -> Node:
        node = Node(self._unique(name or label), type_, label, plugin,
                    dict(control or {}), config)
        for port, value in node.control.items():
            if isinstance(value, PV):
                self.bindings.append(Binding(value.key, node.name, port, value.fn))
        self.nodes.append(node)
        return node

    def link(self, out: Signal, inp: str) -> None:
        self.links.append((out, inp))

    def builtin(self, label: str, x: Signal | None, name: str | None = None,
                in_port: str = "In", **control) -> Node:
        node = self._node("builtin", label, name=name, control=control)
        if x is not None:
            self.link(x, node[in_port])
        return node

    def mix(self, inputs: Sequence[tuple[Signal, float]], name: str = "mix") -> Signal:
        """Sum up to 8 signals with individual gains."""
        if not 1 <= len(inputs) <= 8:
            raise ValueError("mixer takes 1..8 inputs")
        control = {f"Gain {i + 1}": g for i, (_, g) in enumerate(inputs)}
        node = self._node("builtin", "mixer", name=name, control=control)
        for i, (sig, _) in enumerate(inputs):
            self.link(sig, node[f"In {i + 1}"])
        return node["Out"]

    def dry_wet(self, dry: Signal, wet: Signal, mix: float, name: str = "drywet") -> Signal:
        """Crossfade: mix=0 -> all dry, mix=1 -> all wet."""
        return self.mix([(dry, 1.0 - mix), (wet, mix)], name=name)

    # -- filters -----------------------------------------------------------

    def _bq(self, kind: str, x: Signal, freq, q=0.707, gain=0.0, name=None) -> Signal:
        return self.builtin(f"bq_{kind}", x, name=name or kind,
                            Freq=freq, Q=q, Gain=gain)["Out"]

    def lowpass(self, x, freq, q=0.707, name=None):   return self._bq("lowpass", x, freq, q, name=name)
    def highpass(self, x, freq, q=0.707, name=None):  return self._bq("highpass", x, freq, q, name=name)
    def bandpass(self, x, freq, q=0.707, name=None):  return self._bq("bandpass", x, freq, q, name=name)
    def peak(self, x, freq, gain, q=1.0, name=None):  return self._bq("peaking", x, freq, q, gain, name=name)
    def lowshelf(self, x, freq, gain, q=0.707, name=None):  return self._bq("lowshelf", x, freq, q, gain, name=name)
    def highshelf(self, x, freq, gain, q=0.707, name=None): return self._bq("highshelf", x, freq, q, gain, name=name)

    # -- amplitude ---------------------------------------------------------

    def gain(self, x: Signal, mult, name: str = "gain") -> Signal:
        return self.builtin("linear", x, name=name, Mult=mult, Add=0.0)["Out"]

    def clip(self, x: Signal, limit, name: str = "clip") -> Signal:
        return self.builtin("clamp", x, name=name, Min=-limit, Max=limit)["Out"]

    def drive(self, x: Signal, amount, ceiling: float = 0.5, out: float = 1.0,
              name: str = "drive") -> Signal:
        """Hard-clipping overdrive: boost, clip, then restore level."""
        x = self.gain(x, amount, name=f"{name}_pre")
        x = self.clip(x, ceiling, name=f"{name}_clip")
        return self.gain(x, out / ceiling, name=f"{name}_post")

    # -- modulation --------------------------------------------------------

    def osc(self, freq, ampl=1.0, offset=0.0, name: str = "osc") -> Signal:
        return self._node("builtin", "sine", name=name,
                          control={"Freq": freq, "Ampl": ampl, "Offset": offset})["Out"]

    def ringmod(self, x: Signal, freq, mix=1.0, name: str = "ring") -> Signal:
        carrier = self.osc(freq, name=f"{name}_osc")
        mult = self._node("builtin", "mult", name=name)
        self.link(x, mult["In 1"])
        self.link(carrier, mult["In 2"])
        return self.dry_wet(x, mult["Out"], mix, name=f"{name}_mix")

    def tremolo(self, x: Signal, rate, depth=0.5, name: str = "trem") -> Signal:
        """Amplitude modulation between (1 - depth) and 1."""
        carrier = self.osc(rate, ampl=depth / 2.0, offset=1.0 - depth / 2.0, name=f"{name}_osc")
        mult = self._node("builtin", "mult", name=name)
        self.link(x, mult["In 1"])
        self.link(carrier, mult["In 2"])
        return mult["Out"]

    # -- time based --------------------------------------------------------

    def delay(self, x: Signal, seconds, max_delay: float = 2.0, name: str = "delay") -> Signal:
        node = self._node("builtin", "delay", name=name,
                          config={"max-delay": float(max(max_delay, float(seconds) + 0.01))},
                          control={"Delay (s)": seconds})
        self.link(x, node["In"])
        return node["Out"]

    def comb(self, x: Signal, ms, mix=0.5, name: str = "comb") -> Signal:
        """Feed-forward comb filter: metallic / tube-like resonance."""
        d = self.delay(x, ms / 1000.0, max_delay=0.1, name=f"{name}_dly")
        return self.mix([(x, 1.0 - mix * 0.5), (d, mix)], name=f"{name}_mix")

    def echo(self, x: Signal, taps: Iterable[tuple[float, float]], dry=1.0,
             name: str = "echo") -> Signal:
        """Multi-tap echo. ``taps`` is a list of (seconds, gain), max 7."""
        taps = list(taps)
        if len(taps) > 7:
            raise ValueError("echo supports at most 7 taps")
        inputs = [(x, dry)]
        for i, (t, g) in enumerate(taps):
            inputs.append((self.delay(x, t, max_delay=5.0, name=f"{name}_t{i + 1}"), g))
        return self.mix(inputs, name=f"{name}_mix")

    def reverb(self, x: Signal, space: str, wet=0.3, dry=1.0, name: str = "verb") -> Signal:
        path = str(irgen.ir_path(space))
        node = self._node("builtin", "convolver", name=name,
                          config={"filename": path, "blocksize": 64, "tailsize": 4096,
                                  "gain": 1.0})
        self.link(x, node["In"])
        return self.mix([(x, dry), (node["Out"], wet)], name=f"{name}_mix")

    # -- pitch -------------------------------------------------------------

    def pitch(self, x: Signal, semitones=0.0, cents=0.0, octaves: int = 0,
              formant: bool = False, name: str = "pitch") -> Signal:
        if not isinstance(semitones, PV) and abs(semitones) > 12:
            octaves += int(semitones / 12)
            semitones = semitones - 12 * int(semitones / 12)
        control = {
            "Semitones": semitones,
            "Cents": cents,
            "Octaves": float(octaves),
            "Formant Preserving": 1.0 if formant else 0.0,
            "Wet-Dry Mix": 0.0,
        }
        if self.pitch_engine.label.startswith("rubberband-pitchshifter"):
            control["Crispness"] = 3.0
        node = self._node("ladspa", self.pitch_engine.label, name=name,
                          plugin=self.pitch_engine.plugin, control=control)
        self.link(x, node["Input"])
        return node["Output"]

    def detuned(self, x: Signal, voices: Sequence[tuple[float, float, float]],
                dry=1.0, name: str = "voices") -> Signal:
        """Parallel pitch-shifted copies: list of (semitones+cents/100, delay_s, gain)."""
        inputs = [(x, dry)] if dry else []
        for i, (shift, dly, g) in enumerate(voices):
            semis = int(round(shift))
            cents = round((shift - semis) * 100)
            v = self.pitch(x, semis, cents, name=f"{name}_p{i + 1}")
            if dly:
                v = self.delay(v, dly, max_delay=0.5, name=f"{name}_d{i + 1}")
            inputs.append((v, g))
        return self.mix(inputs, name=f"{name}_mix")

    # -- output ------------------------------------------------------------

    def finish(self, x: Signal, volume=1.0) -> Signal:
        """Master gain plus a safety limiter so nothing blows out ears."""
        x = self.gain(x, volume, name="master")
        x = self.clip(x, 0.98, name="limiter")
        self.output = x
        return x

    def to_spa(self) -> dict:
        return {
            "nodes": [n.to_spa() for n in self.nodes],
            "links": [{"output": o, "input": i} for o, i in self.links],
            "inputs": [self.entry["In"]],
            "outputs": [self.output],
        }
