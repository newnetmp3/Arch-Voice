"""Voice presets.

Each preset is a function ``(g: Graph, x: Signal, p: Params) -> Signal``
registered with :func:`preset`. Parameters declared with :class:`Param`
show up as sliders in the GUI and as ``key=value`` options on the CLI;
those that map straight onto a control port can be changed live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .dsp import PV, Graph, Signal


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    min: float
    max: float
    default: float
    step: float = 0.01
    unit: str = ""
    digits: int = 2


VOLUME = Param("volume", "Output volume", 0.0, 3.0, 1.0, 0.05, "×")


@dataclass
class Preset:
    id: str
    name: str
    category: str
    description: str
    build: Callable[[Graph, Signal, "Params"], Signal]
    params: list[Param] = field(default_factory=list)
    icon: str = "audio-input-microphone-symbolic"

    def all_params(self) -> list[Param]:
        return [*self.params, VOLUME]

    def defaults(self) -> dict[str, float]:
        return {p.key: p.default for p in self.all_params()}


class Params:
    """Attribute access to parameter values, as PV instances."""

    def __init__(self, preset: Preset, values: dict[str, float] | None = None):
        self._values: dict[str, PV] = {}
        values = values or {}
        for p in preset.all_params():
            v = float(values.get(p.key, p.default))
            v = min(p.max, max(p.min, v))
            self._values[p.key] = PV(v, p.key)

    def __getattr__(self, key: str) -> PV:
        try:
            return self._values[key]
        except KeyError:
            raise AttributeError(key) from None


CATEGORIES = ["Voice", "Creatures", "Robotic", "Comms", "Spaces", "Fun", "Utility"]
PRESETS: dict[str, Preset] = {}


def preset(id: str, name: str, category: str, description: str,
           params: list[Param] | None = None, icon: str | None = None):
    assert category in CATEGORIES, category

    def deco(fn):
        p = Preset(id, name, category, description, fn, params or [])
        if icon:
            p.icon = icon
        PRESETS[id] = p
        return fn

    return deco


def pitch_param(default: float, lo: float = -12, hi: float = 12) -> Param:
    return Param("pitch", "Pitch", lo, hi, default, 1, " st", 0)


def mix_param(label: str, default: float, key: str = "mix") -> Param:
    return Param(key, label, 0.0, 1.0, default, 0.05)


# ==========================================================================
# Voice — natural-ish pitch and timbre changes

@preset("deeper", "Deeper Voice", "Voice",
        "Lower pitch with formants preserved. Sounds like you, but deeper.",
        [pitch_param(-4, -12, 0)])
def _deeper(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    return g.lowshelf(x, 180, 2.5)


@preset("higher", "Higher Voice", "Voice",
        "Raise pitch while keeping your natural vocal character.",
        [pitch_param(4, 0, 12)])
def _higher(g, x, p):
    x = g.highpass(x, 120)
    x = g.pitch(x, p.pitch, formant=True)
    return g.highshelf(x, 4000, 2.0)


@preset("child", "Child", "Voice",
        "Higher pitch and smaller-sounding vocal tract.",
        [pitch_param(5, 0, 12)])
def _child(g, x, p):
    x = g.highpass(x, 200)
    x = g.pitch(x, p.pitch, formant=False)
    return g.peak(x, 2800, 3.0, q=1.2)


@preset("elder", "Elder", "Voice",
        "Slightly lower, thinner and shaky.",
        [pitch_param(-1, -6, 3),
         Param("shake", "Shakiness", 0.0, 0.8, 0.3),
         Param("rate", "Shake speed", 2.0, 9.0, 5.5, 0.1, " Hz", 1)])
def _elder(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    x = g.highpass(x, 220)
    x = g.lowpass(x, 6000)
    x = g.peak(x, 1300, 3.0, q=1.5)
    return g.tremolo(x, p.rate, p.shake)


@preset("narrator", "Movie Narrator", "Voice",
        "Deep, warm trailer voice with a touch of room.",
        [pitch_param(-3, -8, 0)])
def _narrator(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    x = g.lowshelf(x, 140, 5.0)
    x = g.peak(x, 3000, 2.0)
    x = g.drive(x, 1.6, ceiling=0.8, out=0.85)
    return g.reverb(x, "hall", wet=0.12)


# ==========================================================================
# Creatures

@preset("demon", "Demon", "Creatures",
        "An octave down, distorted, echoing from below.",
        [pitch_param(-12, -12, 0), mix_param("Reverb", 0.3, "verb"),
         Param("drive", "Distortion", 1.0, 10.0, 3.0, 0.1, "×", 1)])
def _demon(g, x, p):
    low = g.pitch(x, p.pitch, formant=False, name="pitch")
    sub = g.pitch(x, -12, octaves=-1, name="sub")
    x = g.mix([(low, 1.0), (sub, 0.35)])
    x = g.lowshelf(x, 150, 6.0)
    x = g.drive(x, p.drive, ceiling=0.45, out=0.45)
    x = g.lowpass(x, 5000)
    return g.reverb(x, "cave", wet=p.verb)


@preset("monster", "Monster", "Creatures",
        "Growling beast with a gritty, rumbling texture.",
        [pitch_param(-9, -12, 0), Param("growl", "Growl", 0.0, 1.0, 0.35)])
def _monster(g, x, p):
    x = g.pitch(x, p.pitch, formant=False)
    x = g.ringmod(x, 38, p.growl)
    x = g.drive(x, 4.0, ceiling=0.4, out=0.4)
    x = g.lowshelf(x, 200, 4.0)
    return g.lowpass(x, 3800)


@preset("giant", "Giant", "Creatures",
        "Huge, slow and booming.",
        [pitch_param(-7, -12, 0), mix_param("Reverb", 0.25, "verb")])
def _giant(g, x, p):
    x = g.pitch(x, p.pitch, formant=False)
    x = g.lowshelf(x, 160, 5.0)
    x = g.lowpass(x, 6000)
    return g.reverb(x, "hall", wet=p.verb)


@preset("troll", "Troll", "Creatures",
        "Low, nasal and grumpy.",
        [pitch_param(-5, -12, 0)])
def _troll(g, x, p):
    x = g.pitch(x, p.pitch, formant=False)
    x = g.peak(x, 1000, 8.0, q=2.0)
    x = g.peak(x, 250, 4.0, q=1.0)
    return g.drive(x, 2.0, ceiling=0.6, out=0.55)


@preset("goblin", "Goblin", "Creatures",
        "High, nasal and sneaky.",
        [pitch_param(6, 0, 12)])
def _goblin(g, x, p):
    x = g.highpass(x, 250)
    x = g.pitch(x, p.pitch, formant=False)
    x = g.peak(x, 1500, 9.0, q=2.0)
    return g.drive(x, 1.8, ceiling=0.7, out=0.9)


@preset("alien", "Alien", "Creatures",
        "Warbling extraterrestrial with metallic overtones.",
        [pitch_param(4), Param("ring", "Ring frequency", 50, 1500, 320, 5, " Hz", 0),
         mix_param("Ring mix", 0.35)])
def _alien(g, x, p):
    x = g.pitch(x, p.pitch, formant=False)
    x = g.ringmod(x, p.ring, p.mix)
    x = g.comb(x, 3.0, 0.4)
    x = g.reverb(x, "metal", wet=0.15)
    return g.gain(x, 1.4, name="makeup")


@preset("ghost", "Ghost", "Creatures",
        "Thin, airy voice haunting a huge empty hall.",
        [pitch_param(2, -6, 12), mix_param("Reverb", 0.6, "verb")])
def _ghost(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    x = g.highpass(x, 450)
    x = g.tremolo(x, 4.0, 0.25)
    x = g.echo(x, [(0.33, 0.35), (0.66, 0.18)])
    x = g.reverb(x, "cathedral", wet=p.verb, dry=0.6)
    return g.gain(x, 1.4, name="makeup")


@preset("zombie", "Zombie", "Creatures",
        "Groaning, wobbling undead.",
        [pitch_param(-3, -12, 3)])
def _zombie(g, x, p):
    x = g.pitch(x, p.pitch, formant=False)
    x = g.lowpass(x, 2500)
    x = g.tremolo(x, 7.0, 0.45)
    x = g.drive(x, 3.0, ceiling=0.5, out=0.5)
    return g.reverb(x, "room", wet=0.2)


@preset("insect", "Insect", "Creatures",
        "Tiny, buzzing bug voice.",
        [pitch_param(12, 0, 12), Param("buzz", "Buzz", 50, 600, 220, 5, " Hz", 0)])
def _insect(g, x, p):
    x = g.highpass(x, 400)
    x = g.pitch(x, p.pitch, formant=False)
    x = g.ringmod(x, p.buzz, 0.5)
    x = g.highpass(x, 900)
    return g.gain(x, 2.5, name="makeup")


# ==========================================================================
# Robotic

@preset("robot", "Robot", "Robotic",
        "Classic metallic robot voice.",
        [Param("freq", "Ring frequency", 20, 400, 70, 1, " Hz", 0),
         mix_param("Robot mix", 0.75)], icon="applications-science-symbolic")
def _robot(g, x, p):
    x = g.highpass(x, 140)
    x = g.ringmod(x, p.freq, p.mix)
    x = g.comb(x, 6.0, 0.5, name="combA")
    x = g.comb(x, 13.0, 0.3, name="combB")
    x = g.peak(x, 1800, 3.0)
    return g.gain(x, 1.6, name="makeup")


@preset("dalek", "Dalek-style", "Robotic",
        "Harsh, buzzing 30 Hz ring modulator villain. EX-TER-MI-NATE.",
        [Param("freq", "Ring frequency", 15, 80, 30, 1, " Hz", 0)])
def _dalek(g, x, p):
    x = g.highpass(x, 200)
    x = g.ringmod(x, p.freq, 1.0)
    x = g.drive(x, 2.5, ceiling=0.5, out=0.55)
    return g.peak(x, 1200, 4.0)


@preset("cyborg", "Cyborg", "Robotic",
        "Half human, half machine.",
        [pitch_param(-3, -12, 12), mix_param("Machine", 0.4)])
def _cyborg(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    x = g.ringmod(x, 110, p.mix)
    x = g.comb(x, 5.0, 0.5)
    x = g.reverb(x, "metal", wet=0.12)
    return g.gain(x, 1.4, name="makeup")


@preset("android", "Android", "Robotic",
        "Clean, synthetic, slightly higher assistant voice.",
        [pitch_param(2, -6, 12)])
def _android(g, x, p):
    x = g.pitch(x, p.pitch, formant=True)
    x = g.comb(x, 2.5, 0.6)
    x = g.highshelf(x, 5000, 4.0)
    x = g.highpass(x, 160)
    return g.gain(x, 1.4, name="makeup")


@preset("glitchbot", "Glitch Bot", "Robotic",
        "Broken robot: stuttering amplitude and crushed tone.",
        [Param("rate", "Stutter speed", 4, 40, 16, 1, " Hz", 0)])
def _glitchbot(g, x, p):
    x = g.highpass(x, 200)
    x = g.ringmod(x, 95, 0.6)
    x = g.tremolo(x, p.rate, 0.9)
    return g.drive(x, 5.0, ceiling=0.3, out=0.5)


# ==========================================================================
# Comms

@preset("telephone", "Telephone", "Comms",
        "Narrow-band landline call.", icon="call-start-symbolic")
def _telephone(g, x, p):
    x = g.highpass(x, 300, name="hp1")
    x = g.highpass(x, 300, name="hp2")
    x = g.lowpass(x, 3400, name="lp1")
    x = g.lowpass(x, 3400, name="lp2")
    x = g.peak(x, 1700, 3.0)
    return g.drive(x, 2.0, ceiling=0.6, out=0.9)


@preset("walkie", "Walkie-Talkie", "Comms",
        "Crunchy two-way radio.",
        [Param("drive", "Crunch", 1.0, 12.0, 6.0, 0.1, "×", 1)])
def _walkie(g, x, p):
    x = g.highpass(x, 500, name="hp1")
    x = g.highpass(x, 500, name="hp2")
    x = g.lowpass(x, 2600, name="lp1")
    x = g.lowpass(x, 2600, name="lp2")
    x = g.peak(x, 1400, 6.0)
    return g.drive(x, p.drive, ceiling=0.3, out=0.5)


@preset("megaphone", "Megaphone", "Comms",
        "Loud, honky bullhorn.")
def _megaphone(g, x, p):
    x = g.highpass(x, 600, name="hp1")
    x = g.highpass(x, 600, name="hp2")
    x = g.lowpass(x, 3500)
    x = g.peak(x, 2000, 8.0, q=1.2)
    x = g.drive(x, 5.0, ceiling=0.35, out=0.55)
    x = g.comb(x, 1.5, 0.3)
    return g.reverb(x, "room", wet=0.15)


@preset("oldradio", "Old Radio", "Comms",
        "1940s AM broadcast.")
def _oldradio(g, x, p):
    x = g.highpass(x, 400)
    x = g.lowpass(x, 3000, name="lp1")
    x = g.lowpass(x, 3000, name="lp2")
    x = g.peak(x, 900, 4.0)
    x = g.drive(x, 3.0, ceiling=0.5, out=0.85)
    return g.tremolo(x, 0.7, 0.1)


@preset("spacecomms", "Space Comms", "Comms",
        "Mission control transmission from orbit.")
def _spacecomms(g, x, p):
    x = g.highpass(x, 700)
    x = g.lowpass(x, 3000)
    x = g.ringmod(x, 1200, 0.15)
    x = g.comb(x, 4.0, 0.3)
    return g.drive(x, 3.0, ceiling=0.4, out=0.8)


# ==========================================================================
# Spaces

@preset("room", "Small Room", "Spaces",
        "A touch of natural room ambience.",
        [mix_param("Reverb", 0.25, "verb")])
def _room(g, x, p):
    return g.reverb(x, "room", wet=p.verb)


@preset("bathroom", "Bathroom", "Spaces",
        "Bright, tiled, singing-in-the-shower echo.",
        [mix_param("Reverb", 0.4, "verb")])
def _bathroom(g, x, p):
    return g.reverb(x, "bathroom", wet=p.verb)


@preset("cave", "Cave", "Spaces",
        "Deep, dark cavern with slap-back echoes.",
        [mix_param("Reverb", 0.45, "verb"), mix_param("Echo", 0.3, "echo")])
def _cave(g, x, p):
    x = g.echo(x, [(0.18, p.echo), (0.37, p.echo * 0.55), (0.56, p.echo * 0.3)])
    return g.reverb(x, "cave", wet=p.verb)


@preset("cathedral", "Cathedral", "Spaces",
        "Enormous stone hall with a long tail.",
        [mix_param("Reverb", 0.5, "verb")])
def _cathedral(g, x, p):
    return g.reverb(x, "cathedral", wet=p.verb, dry=0.8)


@preset("stadium", "Stadium PA", "Spaces",
        "Announcer echoing around an arena.")
def _stadium(g, x, p):
    x = g.highpass(x, 180)
    x = g.peak(x, 2500, 4.0)
    x = g.echo(x, [(0.28, 0.45), (0.57, 0.28), (0.86, 0.16), (1.15, 0.08)])
    return g.reverb(x, "hall", wet=0.35)


@preset("underwater", "Underwater", "Spaces",
        "Muffled and wobbly, like talking from the bottom of a pool.",
        [Param("cutoff", "Muffle", 200, 1500, 550, 10, " Hz", 0)])
def _underwater(g, x, p):
    x = g.pitch(x, -1, formant=True)
    x = g.lowpass(x, p.cutoff, q=1.2, name="lp1")
    x = g.lowpass(x, p.cutoff * 1.3, name="lp2")
    x = g.peak(x, 300, 4.0)
    x = g.tremolo(x, 5.5, 0.35)
    x = g.reverb(x, "tank", wet=0.4)
    return g.gain(x, 1.6, name="makeup")


# ==========================================================================
# Fun / musical

@preset("chipmunk", "Chipmunk", "Fun",
        "Fast-talking cartoon squeak.",
        [pitch_param(9, 0, 12)])
def _chipmunk(g, x, p):
    x = g.highpass(x, 150)
    return g.pitch(x, p.pitch, formant=False)


@preset("helium", "Helium", "Fun",
        "Just inhaled a party balloon.",
        [pitch_param(5, 0, 12)])
def _helium(g, x, p):
    x = g.highpass(x, 250)
    x = g.pitch(x, p.pitch, formant=False)
    return g.highshelf(x, 3500, 3.0)


@preset("chorus", "Chorus / Doubler", "Fun",
        "Thicken your voice with slightly detuned copies.",
        [mix_param("Width", 0.6)])
def _chorus(g, x, p):
    return g.detuned(x, [(0.12, 0.018, p.mix), (-0.12, 0.027, p.mix)], dry=1.0)


@preset("choir", "Harmony Choir", "Fun",
        "You, plus an octave below and a fifth above.",
        [mix_param("Harmony", 0.6)])
def _choir(g, x, p):
    x = g.detuned(x, [(-12, 0.0, p.mix), (7, 0.012, p.mix * 0.8), (12, 0.02, p.mix * 0.4)], dry=1.0)
    return g.reverb(x, "hall", wet=0.25)


@preset("echo", "Echo", "Fun",
        "Big canyon echo.",
        [Param("time", "Delay", 0.1, 0.9, 0.3, 0.01, " s", 2),
         mix_param("Feedback", 0.5, "fb")])
def _echo(g, x, p):
    return g.echo(x, [(p.time * (i + 1), p.fb ** (i + 1)) for i in range(5)])


@preset("overdrive", "Overdrive", "Fun",
        "Screaming-into-a-guitar-amp distortion.",
        [Param("drive", "Drive", 1.0, 20.0, 8.0, 0.5, "×", 1)])
def _overdrive(g, x, p):
    x = g.highpass(x, 150)
    x = g.drive(x, p.drive, ceiling=0.25, out=0.45)
    return g.lowpass(x, 5000)


# ==========================================================================
# Utility

@preset("clean", "Clean (Bypass)", "Utility",
        "Your normal voice, with rumble removed.",
        icon="object-select-symbolic")
def _clean(g, x, p):
    return g.highpass(x, 80)


@preset("broadcast", "Broadcast", "Utility",
        "Radio-host polish: warm lows, clear presence, no rumble.",
        [Param("warmth", "Warmth", 0.0, 8.0, 3.0, 0.5, " dB", 1),
         Param("presence", "Presence", 0.0, 8.0, 4.0, 0.5, " dB", 1)])
def _broadcast(g, x, p):
    x = g.highpass(x, 90)
    x = g.peak(x, 150, p.warmth, q=0.8, name="warmth")
    x = g.peak(x, 350, -2.0, q=1.0, name="mud")
    x = g.peak(x, 3500, p.presence, q=0.9, name="presence")
    x = g.highshelf(x, 9000, 2.0)
    return g.drive(x, 1.3, ceiling=0.9, out=0.95)


def build(preset_id: str, values: dict[str, float] | None = None,
          pitch_engine=None) -> Graph:
    preset = PRESETS[preset_id]
    g = Graph(pitch_engine)
    p = Params(preset, values)
    out = preset.build(g, g.input, p)
    g.finish(out, p.volume)
    return g


def by_category() -> dict[str, list[Preset]]:
    out: dict[str, list[Preset]] = {c: [] for c in CATEGORIES}
    for pr in PRESETS.values():
        out[pr.category].append(pr)
    return out
