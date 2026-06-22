**English** | [한국어](README.ko.md)

# Tapo Ambilight

![demo](docs/images/demo.gif)

Sync your PC screen to a **TP-Link Tapo L930** RGBIC light strip — **per-zone**
(a color gradient that follows the edges of your screen), with **no blinking**.

The L930 has no official "screen sync" / entertainment mode. This project drives
it through the local API using `set_segment_effect`, which is the one path that
updates segment colors **in place without restarting the effect** (see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the reverse-engineering notes).

> **Reality check:** the bulb's local protocol caps out around **4–5 updates per
> second** (≈200 ms per encrypted round-trip, no streaming API). This is great
> for calm, slowly-changing ambient lighting; it is **not** smooth enough for
> fast games or action video. That's a hardware limitation, not a bug.

## Features

- Per-zone perimeter sync — maps the strip's physical loop to your screen edges
- No blinking (uses `set_segment_effect` with a fixed effect id)
- Desktop GUI: connection, lighting sliders, live fps/latency readout
- Guided **Calibration Wizard** — tag the four screen corners + the strip's end
- **System tray** background mode + optional **Start with Windows**
- Headless **CLI** for running without a GUI
- Colored-pixel-gated extraction (dark/neutral screens stay dim white, not noisy)

## Requirements

- Windows (the tray + autostart use Windows APIs; the engine/CLI are cross-platform)
- Python 3.10+
- A Tapo L930, with **Me → Third-Party Service → Third-Party Compatibility** enabled in the
  Tapo app
- The strip's local IP (reserve it in your router so it doesn't change)

## Install

```bash
pip install -r requirements.txt
```

`pystray` and `pillow` are only needed for the tray icon; without them the app
still runs and "Hide to tray" falls back to a normal minimize.

## Setup

```bash
cp config.example.json config.json   # then edit credentials (config.json is gitignored)
```

Fill in `username`, `password`, and `ip`. Leave the rest at defaults for now.

## Run

**GUI** (recommended):

```bash
pythonw app.py        # Windows, no console window  (or double-click run.bat)
python  app.py        # with console logs
```

1. **Connection** tab → enter credentials → **Connect / Test**
2. **Calibration** tab → **Run Calibration Wizard**
   - One white LED lights up. Step it around the loop with **Next / Prev**.
   - When it sits at each screen corner, tag **Top-Left / Top-Right /
     Bottom-Left / Bottom-Right**. At the strip's physical end, **Mark END**.
   - **Save & Close**.
3. **▶ Start**.

**CLI** (headless):

```bash
python cli.py calibrate   # interactive calibration in the terminal
python cli.py             # run the sync, prints effective fps + send latency
```

## Configuration

All values live in `config.json` and are editable from the GUI.

| Key | Meaning | Suggested |
|---|---|---|
| `brightness` | overall brightness | 70–90 |
| `saturation_boost` | punchier colors | 1.5–2.0 |
| `num_bands` | number of zones sent to the strip | 16–25 |
| `target_fps` | update target (real ceiling is ~4–5) | 12–20 |
| `smoothing` | transition easing (higher = smoother but laggier) | 0.55–0.7 |
| `min_change` | only send when a band changes by this much | 1–4 |
| `min_value` | minimum brightness floor | 10 |
| `band_frac` | how deep from the edge to sample color | 0.15 |
| `display_index` | 0 = all monitors, 1/2 = individual | 0 |
| `corners`, `last_segment` | calibration result — **don't hand-edit** | from wizard |

`corners` / `last_segment` come from calibration. Re-run the wizard only if you
physically re-route the strip.

## Project layout

```
app.py                 GUI desktop app (tray, settings, calibration wizard)
engine.py              sync engine: capture -> extract -> map -> set_segment_effect
cli.py                 headless command-line version (calibrate / run)
extras/solid_sync.py   single-color whole-strip sync (faster, smoother, not per-zone)
config.example.json    template config (copy to config.json)
docs/PROTOCOL.md       how no-blink per-zone updates were found + the fps ceiling
run.bat                no-console launcher for Windows
```

## Why so slow / can it be smoother?

No. Each update is a separate encrypted KLAP round-trip (~200 ms), and the L930
exposes no low-latency streaming mode. Lowering `num_bands` doesn't help — the
cost is protocol overhead, not payload. If you need truly fluid sync, that needs
hardware with a streaming entertainment protocol (e.g. Philips Hue Entertainment
or Govee DreamView). For ambient use, raise `smoothing` and enjoy. See
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Built on the [`tapo`](https://github.com/mihai-dinculescu/tapo) Python library
(MIT) and [`mss`](https://github.com/BoboTiG/python-mss) (MIT) for screen
capture. Both are installed via pip and not redistributed here.

> Not affiliated with or endorsed by TP-Link. "Tapo" is a trademark of its
> respective owner. Use at your own risk.
