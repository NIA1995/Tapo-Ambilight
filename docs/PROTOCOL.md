# Protocol notes — how per-zone updates work without blinking

These are the findings that make this project work. They are not documented by
TP-Link and were established empirically against an L930 (EU, fw 1.4.3, 50
physical segments). They may differ on other firmware or models.

## The core problem

The goal is to push a **different color to each segment** of the strip, **many
times per second**, **without the strip blinking** on every update.

The obvious API, `set_lighting_effect` with a `Static`-type custom effect, does
render per-segment colors — but the device **restarts the effect** on every
re-apply, producing a visible blink. Pinning a fixed effect `id` stops the
blink but also stops updates from taking effect. Adding `transition` / `fade_off`
breaks per-segment rendering entirely. That is a dead end for live sync.

## The fix: `set_segment_effect`

`set_segment_effect` maps to a **different device method**,
`ApplySegmentEffectRule`, and takes a `SegmentEffect` request. With a **fixed
`id`**, the device **updates the colors in place with no restart / no blink**,
while still accepting fresh colors on every call.

Key construction (Python `tapo` library):

```python
from tapo.requests import SegmentEffect, SegmentEffectType

# The static "Color Painting" type the app uses is the hidden enum member None.
NONE = getattr(SegmentEffectType, "None")

effect = (
    SegmentEffect("screen-sync", NONE, is_custom=True, enabled=True,
                  brightness=brightness, display_colors=display_colors)  # <= 4 entries
    .with_segments(band_end_indices)   # cumulative band-END indices
    .with_states(per_band_states)      # one (H, S, V, colortemp=0) per band
    .with_id("TapoStrip_screensync")   # FIXED id  ->  in-place update, no blink
)
await device.set_segment_effect(effect)
```

### Device rules confirmed by testing

- `display_colors` **must be ≤ 4 entries** (5+ returns error `-1008 PARAMS`).
- `states` and `segments` may go up to the full 50 bands.
- `with_segments` expects **cumulative band-end indices**, e.g. 5 even bands →
  `[9, 19, 29, 39, 49]`; 50 bands → `[0, 1, …, 49]`.
- A **fixed `id`** is what suppresses the blink. A changing id re-creates the
  effect each time and blinks.

Helper used in this repo:

```python
def band_segments(n, leds=50):
    return [round((i + 1) * leds / n) - 1 for i in range(n)]
```

## Performance ceiling

Each update is one encrypted **KLAP** HTTP round-trip to the bulb. Measured on
LAN, a single `set_segment_effect` call takes roughly **~200 ms**, independent of
band count (it is protocol/handshake overhead, not payload size). Screen capture
+ color extraction adds only ~40 ms.

**Net effective rate ≈ 4–5 updates/sec.** There is no low-latency streaming path
(no equivalent of Hue Entertainment / Govee DreamView), so smooth flowing
gradients are not achievable on this hardware. Calm, slowly-changing ambient
content looks best. Reducing `num_bands` does **not** materially reduce the
round-trip time.

## Requirements / gotchas

- In the Tapo app, enable **Me → Tapo Lab → Third-Party Compatibility**, or
  local API auth fails.
- L930 is **2.4 GHz only**; weak RSSI raises round-trip latency.
- Color extraction gates on "colored" pixels (saturation and brightness
  thresholds) so neutral/dark screens render as dim white instead of rainbow
  noise — see `extract_hsv` in `engine.py`.
