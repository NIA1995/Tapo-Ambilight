"""
tapo_sync_solid.py
==================
Single-color screen sync for the Tapo L930. The whole strip takes on the
dominant color of your screen, updated in real time. Uses only the basic
whole-strip control (set_hue_saturation / set_brightness) that we've
confirmed works on your device -- no custom zone effects.

Requires: pip install tapo mss numpy
Config: reads config.json (uses username, password, ip, display_index,
        target_fps, smoothing, saturation_boost, min_value, brightness).
Stop with Ctrl+C.
"""

import asyncio
import json
import os
import colorsys

import numpy as np
import mss

from tapo import ApiClient


def load_config():
    # config.json lives in the repo root (one level up from extras/)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def grab_dominant_hsv(sct, monitor, stride, sat_boost, min_value):
    """Capture monitor, return one (hue, sat, val) for the whole screen.
    Uses a saturation-weighted average so vivid areas dominate over grey UI."""
    frame = np.asarray(sct.grab(monitor))[::stride, ::stride, :3]  # BGRA -> BGR
    frame = frame[:, :, ::-1].reshape(-1, 3) / 255.0               # -> RGB, flat

    # convert all pixels to HSV (vectorized via max/min, cheap enough)
    r, g, b = frame[:, 0], frame[:, 1], frame[:, 2]
    mx = frame.max(axis=1)
    mn = frame.min(axis=1)
    diff = mx - mn
    sat = np.where(mx == 0, 0, diff / np.where(mx == 0, 1, mx))

    # weight hue average by saturation*value so grey pixels barely count
    weight = sat * mx + 1e-6
    # hue per pixel
    hue = np.zeros_like(mx)
    mask = diff > 1e-6
    rc = (mx - r) / np.where(diff == 0, 1, diff)
    gc = (mx - g) / np.where(diff == 0, 1, diff)
    bc = (mx - b) / np.where(diff == 0, 1, diff)
    h = np.where(mx == r, bc - gc, np.where(mx == g, 2.0 + rc - bc, 4.0 + gc - rc))
    hue = (h / 6.0) % 1.0
    hue = np.where(mask, hue, 0.0)

    # circular weighted mean of hue
    ang = hue * 2 * np.pi
    sin = np.average(np.sin(ang), weights=weight)
    cos = np.average(np.cos(ang), weights=weight)
    mean_hue = (np.arctan2(sin, cos) / (2 * np.pi)) % 1.0

    mean_sat = float(np.average(sat, weights=weight))
    mean_val = float(mx.mean())

    H = int(mean_hue * 360)
    S = int(min(100, mean_sat * 100 * sat_boost))
    V = max(min_value, int(mean_val * 100))
    return H, max(1, S), V


def smooth_hsv(prev, cur, alpha):
    if prev is None:
        return cur
    ph, ps, pv = prev
    ch, cs, cv = cur
    dh = ((ch - ph + 540) % 360) - 180
    return (
        int((ph + (1 - alpha) * dh) % 360),
        max(1, int(ps + (1 - alpha) * (cs - ps))),
        int(pv + (1 - alpha) * (cv - pv)),
    )


async def connect(cfg):
    client = ApiClient(cfg["username"], cfg["password"])
    device = await client.l930(cfg["ip"])
    await device.get_device_info()
    return device


async def main():
    cfg = load_config()
    target_fps = float(cfg["target_fps"])
    interval = 1.0 / target_fps
    alpha = float(cfg["smoothing"])
    stride = int(cfg["downsample_stride"])
    sat_boost = float(cfg["saturation_boost"])
    min_value = int(cfg["min_value"])

    print("Connecting...")
    device = await connect(cfg)
    print(f"Connected. Single-color sync @ ~{target_fps:g} fps. Ctrl+C to stop.")

    prev = None
    fail = 0
    with mss.mss() as sct:
        monitor = sct.monitors[int(cfg["display_index"])]
        try:
            while True:
                t0 = asyncio.get_event_loop().time()
                h, s, v = grab_dominant_hsv(sct, monitor, stride, sat_boost, min_value)
                h, s, v = smooth_hsv(prev, (h, s, v), alpha)
                prev = (h, s, v)
                try:
                    # set color, then brightness to follow screen luminance
                    await device.set_hue_saturation(h, s)
                    await device.set_brightness(max(1, v))
                    fail = 0
                except Exception as e:
                    fail += 1
                    print(f"  update failed ({fail}): {e}")
                    if fail >= 5:
                        print("  reconnecting...")
                        try:
                            device = await connect(cfg)
                            fail = 0
                        except Exception:
                            await asyncio.sleep(5)
                elapsed = asyncio.get_event_loop().time() - t0
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            print("\nStopping. Warm solid color.")
            try:
                await device.set_hue_saturation(30, 40)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
