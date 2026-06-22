"""
tapo_ambilight_perimeter.py  (segment-effect, NO-BLINK)
=======================================================
Per-zone screen sync for a Tapo L930 mounted as a LOOP around the monitors.

Uses set_segment_effect (static "Color Painting", SegmentEffectType.None),
which updates IN PLACE with NO blink -- unlike set_lighting_effect. This gives
per-zone + live + blink-free at the same time.

Key device rules discovered:
  - display_colors must be <= 4 entries (the rest live in `states`)
  - `segments` are cumulative band-END indices (50 bands -> [0,1,...,49])
  - a fixed effect id updates in place without blinking

STEP 1 - calibrate once (maps the physical loop to screen corners):
    py .\tapo_ambilight_perimeter.py calibrate
  Controls:  [Enter]=next  [b]=back  [number]=jump
    tr / tl / bl / br = tag current LED as that screen corner
    end = tag current LED as the last mounted LED
    save = write to config.json and exit

STEP 2 - run:
    py .\tapo_ambilight_perimeter.py

Requires: pip install tapo mss numpy.  Stop with Ctrl+C.
"""

import asyncio
import json
import os
import sys

import numpy as np
import mss

from tapo import ApiClient
from tapo.requests import SegmentEffect, SegmentEffectType

NONE = getattr(SegmentEffectType, "None")
LEDS = 50
EFFECT_ID = "TapoStrip_screensync"

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")


def load_config():
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_calibration(corners, last_segment):
    cfg = load_config()
    cfg["corners"] = corners
    cfg["last_segment"] = last_segment
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- segment-effect painting (the no-blink path) ----------
def band_segments(n):
    return [round((i + 1) * LEDS / n) - 1 for i in range(n)]


def aggregate(colors, num_bands):
    """Group the 50 per-LED HSV colors into num_bands by averaging (circular hue)."""
    import math
    seg = band_segments(num_bands)
    out = []
    start = 0
    for end in seg:
        grp = colors[start:end + 1] or [colors[min(start, len(colors)-1)]]
        sx = sum(math.cos(math.radians(h)) for h, s, v in grp)
        sy = sum(math.sin(math.radians(h)) for h, s, v in grp)
        h = int(math.degrees(math.atan2(sy, sx)) % 360)
        s = int(sum(s for _, s, _ in grp) / len(grp))
        v = int(sum(v for _, _, v in grp) / len(grp))
        out.append((h, max(1, s), v))
        start = end + 1
    return out


def seg_painting(states4, brightness, eid=EFFECT_ID):
    """states4: list of (H,S,V,colortemp), one per band. display_colors<=4."""
    seg = band_segments(len(states4))
    dc = states4[:4]
    return (SegmentEffect("screen-sync", NONE, True, True, brightness, dc)
            .with_segments(seg)
            .with_states(states4)
            .with_id(eid))


# ---------- perimeter mapping (LED index -> screen sample rect) ----------
def build_map(N, last_seg, W, H, corners, band_frac):
    bx, by = int(W * band_frac), int(H * band_frac)
    pos = {"TR": (W, 0), "TL": (0, 0), "BL": (0, H), "BR": (W, H)}
    items = sorted(corners.items(), key=lambda kv: kv[1])
    seam = ((pos[items[0][0]][0] + pos[items[-1][0]][0]) / 2,
            (pos[items[0][0]][1] + pos[items[-1][0]][1]) / 2)
    runs = [(0, seam, items[0][1], pos[items[0][0]])]
    for k in range(len(items) - 1):
        runs.append((items[k][1], pos[items[k][0]], items[k + 1][1], pos[items[k + 1][0]]))
    runs.append((items[-1][1], pos[items[-1][0]], last_seg, seam))
    cellw, cellh = W / N, H / N

    def mkrect(x, y):
        dist = {"top": y, "bottom": H - y, "left": x, "right": W - x}
        side = min(dist, key=dist.get)
        if side == "top":
            return (x - cellw, 0, x + cellw, by)
        if side == "bottom":
            return (x - cellw, H - by, x + cellw, H)
        if side == "left":
            return (0, y - cellh, bx, y + cellh)
        return (W - bx, y - cellh, W, y + cellh)

    rects = [None] * N
    for (s0, p0, s1, p1) in runs:
        span = s1 - s0
        for s in range(s0, s1 + 1):
            t = (s - s0) / span if span > 0 else 0.0
            x = p0[0] + (p1[0] - p0[0]) * t
            y = p0[1] + (p1[1] - p0[1]) * t
            x0, y0, x1, y1 = mkrect(x, y)
            rects[s % N] = (max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1)))
    for i in range(N):
        if rects[i] is None:
            rects[i] = (int(seam[0] - 10), 0, int(seam[0] + 10), by)
    return rects


# ---------- color extraction (colored-pixel-gated) ----------
def extract_hsv(px, sat_boost, min_value, min_sat):
    SAT_THRESH, VAL_THRESH = 0.22, 0.18
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    mx = px.max(axis=1)
    diff = mx - px.min(axis=1)
    sat = np.where(mx == 0, 0.0, diff / np.where(mx == 0, 1.0, mx))
    V = max(min_value, int(mx.mean() * 100))
    colored = (sat > SAT_THRESH) & (mx > VAL_THRESH)
    if int(colored.sum()) >= max(2, int(sat.size * 0.015)):
        cr, cg, cb = r[colored], g[colored], b[colored]
        cmx, cdiff, csat = mx[colored], diff[colored], sat[colored]
        w = csat * cmx
        dd = np.where(cdiff == 0, 1.0, cdiff)
        rc, gc, bc = (cmx - cr) / dd, (cmx - cg) / dd, (cmx - cb) / dd
        h = np.where(cmx == cr, bc - gc, np.where(cmx == cg, 2.0 + rc - bc, 4.0 + gc - rc))
        hue = (h / 6.0) % 1.0
        ang = hue * 2 * np.pi
        mean_hue = (np.arctan2(np.average(np.sin(ang), weights=w),
                               np.average(np.cos(ang), weights=w)) / (2 * np.pi)) % 1.0
        S = int(min(100, max(min_sat, float(csat.mean()) * 100 * sat_boost)))
        H = int(mean_hue * 360)
    else:
        H, S = 0, 1
    return (H, max(1, S), V)


def grab(sct, monitor, rects, stride, sat_boost, min_value, min_sat):
    # Keep the full frame as uint8 (cheap); convert ONLY the small sampled
    # edge regions to float. Converting the whole 5120x1440 frame each loop
    # was the main CPU cost dragging the effective fps down.
    frame = np.asarray(sct.grab(monitor))[:, :, :3][:, :, ::-1]  # uint8 RGB
    out = []
    for (x0, y0, x1, y1) in rects:
        region = frame[y0:y1:stride, x0:x1:stride]
        if region.size == 0:
            out.append((0, 1, min_value))
        else:
            px = region.reshape(-1, 3).astype(np.float32) / 255.0
            out.append(extract_hsv(px, sat_boost, min_value, min_sat))
    return out


def smooth(prev, cur, alpha):
    if prev is None:
        return cur
    out = []
    for (ph, ps, pv), (ch, cs, cv) in zip(prev, cur):
        dh = ((ch - ph + 540) % 360) - 180
        out.append((int((ph + (1 - alpha) * dh) % 360),
                    max(1, int(ps + (1 - alpha) * (cs - ps))),
                    int(pv + (1 - alpha) * (cv - pv))))
    return out


def changed_enough(a, b, t):
    if a is None:
        return True
    for (ah, as_, av), (bh, bs, bv) in zip(a, b):
        if abs(((ah - bh + 540) % 360) - 180) > t or abs(as_ - bs) > t or abs(av - bv) > t:
            return True
    return False


async def connect(cfg):
    device = await ApiClient(cfg["username"], cfg["password"]).l930(cfg["ip"])
    await device.on()
    return device


# ---------- calibration (single bright LED marker via segment effect) ----------
def marker_states(i, N=LEDS):
    states = [(0, 0, 3, 0)] * N
    states[i % N] = (0, 0, 100, 0)
    return states


async def calibrate(cfg):
    device = await connect(cfg)
    corners = dict(cfg.get("corners", {}))
    last_segment = int(cfg.get("last_segment", LEDS - 1))
    i = 0
    print("\nCALIBRATION. One bright LED is lit; move it around the loop.")
    print("Tag: tr/tl/bl/br = screen corners, end = last LED, save = finish.\n")
    while True:
        await device.set_segment_effect(seg_painting(marker_states(i), int(cfg["brightness"])))
        cmd = input(f"segment {i}/{LEDS-1}  corners={corners} end={last_segment}  > ").strip().lower()
        if cmd == "":
            i = (i + 1) % LEDS
        elif cmd == "b":
            i = (i - 1) % LEDS
        elif cmd in ("tr", "tl", "bl", "br"):
            corners[cmd.upper()] = i
            print(f"  -> {cmd.upper()} = {i}")
        elif cmd == "end":
            last_segment = i
            print(f"  -> last_segment = {i}")
        elif cmd.isdigit():
            i = int(cmd) % LEDS
        elif cmd in ("save", "q", "done"):
            if all(k in corners for k in ("TR", "TL", "BL", "BR")):
                save_calibration(corners, last_segment)
                print(f"\nSaved: corners={corners}, last_segment={last_segment}")
                print("Now run:  py .\\tapo_ambilight_perimeter.py")
                break
            print("  Need all 4 corners before saving.")
        else:
            print("  keys: Enter/b/number, tr/tl/bl/br, end, save")


async def run(cfg):
    if "corners" not in cfg:
        print("Not calibrated. Run:  py .\\tapo_ambilight_perimeter.py calibrate")
        return
    N = LEDS  # full per-LED resolution; states[i] -> LED i
    last_seg = int(cfg.get("last_segment", LEDS - 1))
    brightness = int(cfg["brightness"]); interval = 1.0 / float(cfg["target_fps"])
    alpha = float(cfg["smoothing"]); stride = int(cfg["downsample_stride"])
    sat_boost = float(cfg["saturation_boost"]); min_value = int(cfg["min_value"])
    min_sat = int(cfg.get("min_sat", 5)); band_frac = float(cfg.get("band_frac", 0.15))
    min_change = int(cfg.get("min_change", 6))
    num_bands = min(int(cfg.get("num_bands", 25)), LEDS)
    device = await connect(cfg)
    with mss.mss() as sct:
        monitor = sct.monitors[int(cfg["display_index"])]
        W, H = monitor["width"], monitor["height"]
        rects = build_map(N, last_seg, W, H, cfg["corners"], band_frac)
        print(f"Connected. NO-BLINK segment sync on {W}x{H}. corners={cfg['corners']}. Ctrl+C to stop.")
        prev = last = None; fail = 0
        sent = 0; t_report = asyncio.get_event_loop().time()
        grab_acc = 0.0; send_acc = 0.0
        try:
            while True:
                t0 = asyncio.get_event_loop().time()
                seq = smooth(prev, grab(sct, monitor, rects, stride, sat_boost, min_value, min_sat), alpha)
                prev = seq
                t_grab = asyncio.get_event_loop().time()
                grab_acc += t_grab - t0
                if changed_enough(last, seq, min_change):
                    bands = aggregate(seq, num_bands)
                    states4 = [(h, s, v, 0) for (h, s, v) in bands]
                    try:
                        await device.set_segment_effect(seg_painting(states4, brightness))
                        last = seq; fail = 0; sent += 1
                        send_acc += asyncio.get_event_loop().time() - t_grab
                    except Exception as e:
                        fail += 1; print(f"  update failed ({fail}): {e}")
                        if fail >= 5:
                            try: device = await connect(cfg); fail = 0
                            except Exception: await asyncio.sleep(5)
                now = asyncio.get_event_loop().time()
                if now - t_report >= 2.0:
                    n = max(1, sent)
                    print(f"  effective fps: {sent / (now - t_report):.1f}  "
                          f"(avg grab {1000*grab_acc/n:.0f}ms, send {1000*send_acc/n:.0f}ms)")
                    sent = 0; grab_acc = 0.0; send_acc = 0.0; t_report = now
                dt = now - t0
                if dt < interval:
                    await asyncio.sleep(interval - dt)
        except KeyboardInterrupt:
            pass
        finally:
            print("\nStopping. Warm solid color.")
            try: await device.set_hue_saturation(30, 40)
            except Exception: pass


async def main():
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1].lower().startswith("cal"):
        await calibrate(cfg)
    else:
        await run(cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
