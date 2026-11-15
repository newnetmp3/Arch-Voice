import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.mkdtemp(prefix="arch-voice-test-")
os.environ["XDG_CACHE_HOME"] = _tmp
os.environ["XDG_CONFIG_HOME"] = _tmp

from arch_voice import irgen, presets, pwconf, spajson  # noqa: E402
from arch_voice.daemon import PITCH_ENGINES, _same_structure  # noqa: E402
from arch_voice.dsp import PV  # noqa: E402
from arch_voice.state import State  # noqa: E402

BUILTIN_PORTS = {
    "copy": {"In", "Out"},
    "mixer": {f"In {i}" for i in range(1, 9)} | {"Out"} | {f"Gain {i}" for i in range(1, 9)},
    "mult": {f"In {i}" for i in range(1, 9)} | {"Out"},
    "sine": {"Out", "Freq", "Ampl", "Offset"},
    "linear": {"In", "Out", "Mult", "Add"},
    "clamp": {"In", "Out", "Min", "Max"},
    "delay": {"In", "Out", "Delay (s)"},
    "convolver": {"In", "Out"},
}
BQ_PORTS = {"In", "Out", "Freq", "Q", "Gain"}
RUBBERBAND_PORTS = {"Input", "Output", "Cents", "Semitones", "Octaves", "Crispness",
                    "Formant Preserving", "Wet-Dry Mix"}


def ports_for(node):
    if node.type == "ladspa":
        return RUBBERBAND_PORTS
    if node.label.startswith("bq_"):
        return BQ_PORTS
    return BUILTIN_PORTS[node.label]


class PresetGraphs(unittest.TestCase):
    def test_enough_presets_in_every_category(self):
        self.assertGreaterEqual(len(presets.PRESETS), 30)
        for cat, items in presets.by_category().items():
            self.assertGreaterEqual(len(items), 2, cat)

    def test_every_preset_builds_a_valid_graph(self):
        for engine in PITCH_ENGINES.values():
            for pid, pr in presets.PRESETS.items():
                with self.subTest(preset=pid, engine=engine.label):
                    g = presets.build(pid, None, engine)
                    names = [n.name for n in g.nodes]
                    self.assertEqual(len(names), len(set(names)), "duplicate node names")
                    by_name = {n.name: n for n in g.nodes}

                    def check_port(ref):
                        node, port = ref.split(":", 1)
                        self.assertIn(node, by_name, ref)
                        self.assertIn(port, ports_for(by_name[node]), ref)

                    inputs = set()
                    for out, inp in g.links:
                        check_port(out)
                        check_port(inp)
                        self.assertNotIn(inp, inputs, f"input {inp} linked twice")
                        inputs.add(inp)
                    for n in g.nodes:
                        for port, value in n.control.items():
                            self.assertIn(port, ports_for(n))
                            self.assertTrue(math.isfinite(float(value)), f"{n.name}:{port}")
                    self.assertTrue(g.output.startswith("limiter:"))
                    conf = pwconf.fx_config(g, 1, pr.name, "alsa_input.test")
                    self.assertIn("libpipewire-module-filter-chain", conf)

    def test_parameters_have_sane_ranges(self):
        for pid, pr in presets.PRESETS.items():
            for prm in pr.all_params():
                with self.subTest(preset=pid, param=prm.key):
                    self.assertLess(prm.min, prm.max)
                    self.assertTrue(prm.min <= prm.default <= prm.max)

    def test_every_parameter_is_used(self):
        for pid, pr in presets.PRESETS.items():
            g = presets.build(pid)
            bound = {b.key for b in g.bindings}
            for prm in pr.all_params():
                with self.subTest(preset=pid, param=prm.key):
                    self.assertIn(prm.key, bound)

    def test_live_bindings_track_values(self):
        pr = presets.PRESETS["robot"]
        g = presets.build("robot", {"mix": 0.25})
        gains = {(b.node, b.port): b.fn(0.25) for b in g.bindings if b.key == "mix"}
        self.assertAlmostEqual(gains[("ring_mix", "Gain 1")], 0.75)
        self.assertAlmostEqual(gains[("ring_mix", "Gain 2")], 0.25)
        self.assertEqual(pr.defaults()["volume"], 1.0)

    def test_param_change_keeps_structure(self):
        a = presets.build("echo", {"time": 0.3})
        b = presets.build("echo", {"time": 0.5})
        self.assertTrue(_same_structure(a, b))

    def test_values_are_clamped(self):
        g = presets.build("deeper", {"pitch": -99})
        pitch = next(n for n in g.nodes if n.type == "ladspa")
        self.assertEqual(float(pitch.control["Semitones"]), -12.0)


class Helpers(unittest.TestCase):
    def test_pv_arithmetic_keeps_binding(self):
        v = PV(0.4, "mix")
        d = (1 - v) * 2
        self.assertAlmostEqual(float(d), 1.2)
        self.assertEqual(d.key, "mix")
        self.assertAlmostEqual(d.fn(0.0), 2.0)
        self.assertAlmostEqual((v ** 2).fn(0.5), 0.25)

    def test_spajson(self):
        text = spajson.dumps({"a": 1, "b c": "x\"y", "l": [spajson.Raw("MONO")], "f": False})
        self.assertIn('"b c" = "x\\"y"', text)
        self.assertIn("[ MONO ]", text)
        self.assertIn("f = false", text)

    def test_mic_config(self):
        conf = pwconf.mic_config()
        self.assertIn(pwconf.MIC_NODE, conf)
        self.assertIn('media.class = "Audio/Source"', conf)

    def test_impulse_response_is_normalised(self):
        samples = irgen.generate(irgen.SPACES["room"], seed=1)
        energy = math.sqrt(sum(s * s for s in samples))
        self.assertAlmostEqual(energy, 1.0, places=2)
        self.assertLessEqual(max(abs(s) for s in samples), 1.0)
        path = irgen.ir_path("room")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_state_roundtrip(self):
        st = State()
        st.set_values("robot", {**presets.PRESETS["robot"].defaults(), "freq": 120.0})
        st.save()
        loaded = State.load()
        self.assertEqual(loaded.values_for("robot")["freq"], 120.0)
        loaded.set_values("robot", presets.PRESETS["robot"].defaults())
        self.assertNotIn("robot", loaded.params)


if __name__ == "__main__":
    unittest.main()
