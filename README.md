# Arch Voice

A real-time voice changer for **Arch Linux + PipeWire**. It creates a virtual
microphone called **“Arch Voice Mic”** that you can select in Discord, OBS,
games, browsers — anything that records audio — and runs your real mic
through one of **38 presets**: pitch shifting, robots, monsters, radios,
reverbs, choirs and more.

- GTK 4 / libadwaita GUI with live sliders for every preset
- Full command-line interface (`arch-voice apply demon pitch=-10`)
- Switch presets without your apps losing the microphone
- “Hear yourself” monitor, input-device selection, high-quality pitch mode
- Optional systemd user service that restores your last preset at login
- Pure PipeWire: no PulseAudio modules, no JACK, no extra audio server

## Install

```bash
git clone https://github.com/newnetmp3/Arch-Voice.git
```

```bash
cd Arch-Voice && makepkg -si
```

To update later, run `git pull && makepkg -si` again, then `arch-voice stop`
(or `systemctl --user restart arch-voice.service`) so the background service
restarts with the new code.

Dependencies (pulled in automatically): `pipewire`, `wireplumber`,
`rubberband-ladspa` (LADSPA pitch shifter), `python-gobject`, `gtk4`, `libadwaita`.

To start the voice changer automatically when you log in:

```bash
systemctl --user enable --now arch-voice.service
```

Without the service, the GUI/CLI simply start the background daemon on demand.

## Usage

Open **Arch Voice** from your app launcher (or run `arch-voice`), click a
preset, then choose **Arch Voice Mic** as the input device in the app you
want to talk in. Use the headphones button to hear yourself — with
headphones, or you will get feedback.

### Command line

```text
arch-voice                      open the GUI
arch-voice list [category]      list presets
arch-voice info <preset>        show a preset's adjustable parameters
arch-voice apply <preset> [key=value ...]
arch-voice set key=value ...    tweak the active preset (live when possible)
arch-voice reset                restore the preset's default parameters
arch-voice bypass               switch to the clean preset
arch-voice inputs               list microphones
arch-voice input <name|default> choose which microphone to process
arch-voice monitor on|off       hear yourself
arch-voice quality fast|hq      Rubber Band R2 (default) or R3 engine
arch-voice status               show what is active
arch-voice test [seconds]       record from the virtual mic and report the level
arch-voice stop                 stop and remove the virtual mic
arch-voice dump <preset>        print the generated PipeWire config
```

Examples:

```bash
arch-voice apply robot freq=45 mix=0.9
```

```bash
arch-voice apply demon pitch=-9 verb=0.5
```

```bash
arch-voice set volume=1.4
```

Parameter tweaks are remembered per preset.

## Presets

| ID | Name | Description |
|---|---|---|
| **Voice** | | |
| `deeper` | Deeper Voice | Lower pitch with formants preserved. Sounds like you, but deeper. |
| `higher` | Higher Voice | Raise pitch while keeping your natural vocal character. |
| `child` | Child | Higher pitch and smaller-sounding vocal tract. |
| `elder` | Elder | Slightly lower, thinner and shaky. |
| `narrator` | Movie Narrator | Deep, warm trailer voice with a touch of room. |
| **Creatures** | | |
| `demon` | Demon | An octave down, distorted, echoing from below. |
| `monster` | Monster | Growling beast with a gritty, rumbling texture. |
| `giant` | Giant | Huge, slow and booming. |
| `troll` | Troll | Low, nasal and grumpy. |
| `goblin` | Goblin | High, nasal and sneaky. |
| `alien` | Alien | Warbling extraterrestrial with metallic overtones. |
| `ghost` | Ghost | Thin, airy voice haunting a huge empty hall. |
| `zombie` | Zombie | Groaning, wobbling undead. |
| `insect` | Insect | Tiny, buzzing bug voice. |
| **Robotic** | | |
| `robot` | Robot | Classic metallic robot voice. |
| `dalek` | Dalek-style | Harsh, buzzing 30 Hz ring modulator villain. EX-TER-MI-NATE. |
| `cyborg` | Cyborg | Half human, half machine. |
| `android` | Android | Clean, synthetic, slightly higher assistant voice. |
| `glitchbot` | Glitch Bot | Broken robot: stuttering amplitude and crushed tone. |
| **Comms** | | |
| `telephone` | Telephone | Narrow-band landline call. |
| `walkie` | Walkie-Talkie | Crunchy two-way radio. |
| `megaphone` | Megaphone | Loud, honky bullhorn. |
| `oldradio` | Old Radio | 1940s AM broadcast. |
| `spacecomms` | Space Comms | Mission control transmission from orbit. |
| **Spaces** | | |
| `room` | Small Room | A touch of natural room ambience. |
| `bathroom` | Bathroom | Bright, tiled, singing-in-the-shower echo. |
| `cave` | Cave | Deep, dark cavern with slap-back echoes. |
| `cathedral` | Cathedral | Enormous stone hall with a long tail. |
| `stadium` | Stadium PA | Announcer echoing around an arena. |
| `underwater` | Underwater | Muffled and wobbly, like talking from the bottom of a pool. |
| **Fun** | | |
| `chipmunk` | Chipmunk | Fast-talking cartoon squeak. |
| `helium` | Helium | Just inhaled a party balloon. |
| `chorus` | Chorus / Doubler | Thicken your voice with slightly detuned copies. |
| `choir` | Harmony Choir | You, plus an octave below and a fifth above. |
| `echo` | Echo | Big canyon echo. |
| `overdrive` | Overdrive | Screaming-into-a-guitar-amp distortion. |
| **Utility** | | |
| `clean` | Clean (Bypass) | Your normal voice, with rumble removed. |
| `broadcast` | Broadcast | Radio-host polish: warm lows, clear presence, no rumble. |

Every preset also has an **Output volume** control and ends with a safety
limiter so nothing can blast your listeners' ears.

## How it works

```
 your mic ──► FX chain (pipewire filter-chain) ──► internal bus ──► Arch Voice Mic ──► apps
               └─ rebuilt on every preset change      (loopback, lives as long as the daemon)
```

- `arch-voice daemon` owns a PipeWire **loopback** whose output side is an
  `Audio/Source` node named `arch_voice_mic`. It stays up the whole time, so
  apps never lose their microphone.
- Each preset is a small Python function that builds a PipeWire
  **filter-chain graph** from PipeWire's builtin nodes (biquad filters,
  mixers, delays, sine oscillators, multipliers, clamps, a convolver) plus the
  **Rubber Band** LADSPA pitch shifter. See `arch_voice/presets.py`.
- On a preset change a new chain is started and linked *before* the old one
  is stopped, so switching is almost gapless (~0.2 s).
- Sliders that map onto a control port are applied live with
  `pw-cli set-param`; anything structural restarts the chain.
- Reverbs use synthetic impulse responses generated on first use and cached
  in `~/.cache/arch-voice/ir/`.
- Settings live in `~/.config/arch-voice/state.json`; child-process logs in
  `~/.cache/arch-voice/logs/`.

### Writing your own preset

Add a function to `arch_voice/presets.py`:

```python
@preset("radio-demon", "Radio Demon", "Creatures",
        "A demon calling in on a walkie-talkie.",
        [pitch_param(-10, -12, 0)])
def _radio_demon(g, x, p):
    x = g.pitch(x, p.pitch)            # p.pitch is live-adjustable
    x = g.bandpass(x, 1200, q=0.8)
    x = g.drive(x, 5.0, ceiling=0.35, out=0.5)
    return g.reverb(x, "cave", wet=0.2)
```

Building blocks: `pitch`, `lowpass`, `highpass`, `bandpass`, `peak`,
`lowshelf`, `highshelf`, `gain`, `clip`, `drive`, `ringmod`, `tremolo`,
`delay`, `comb`, `echo`, `reverb`, `detuned`, `mix`, `dry_wet`.
Run `python -m unittest discover -s tests` to validate the graph.

## Troubleshooting

- **No sound in Discord/OBS:** make sure the app's input device is
  *Arch Voice Mic*, and check `arch-voice status`. In Discord, turn off
  *Noise Suppression* and *Echo Cancellation* for best results — they fight
  heavy effects.
- **Wrong microphone is processed:** `arch-voice inputs`, then
  `arch-voice input <name>`.
- **Pitch presets fail with a LADSPA error:** `sudo pacman -S rubberband-ladspa` (Arch ships the plugin separately from `rubberband`).
- **Latency:** pitch shifting adds roughly 50–100 ms (inherent to real-time
  pitch shifting). Non-pitch presets add next to none.
- **Logs:** `journalctl --user -u arch-voice` (service) or
  `~/.cache/arch-voice/logs/`.

## License

MIT
