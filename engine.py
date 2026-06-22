"""
engine.py  --  Tapo L930 ambilight engine (background-thread controllable)
=========================================================================
Headless core used by the GUI app. Runs an asyncio event loop on a daemon
thread; the GUI submits work (connect / sync / calibration marker) and polls
`engine.status` for display. No blinking: uses set_segment_effect.
"""

import asyncio
import math
import threading

import numpy as np
import mss

from tapo import ApiClient
from tapo.requests import SegmentEffect, SegmentEffectType

NONE = getattr(SegmentEffectType, "None")
LEDS = 50
EFFECT_ID = "TapoStrip_screensync"


# ----------------------------- color / effect -----------------------------
def band_segments(n):
    return [round((i + 1) * LEDS / n) - 1 for i in range(n)]


def seg_painting(states4, brightness, eid=EFFECT_ID):
    seg = band_segments(len(states4))
    dc = states4[:4]  # device requires display_colors <= 4
    return (SegmentEffect("screen-sync", NONE, True, True, brightness, dc)
            .with_segments(seg)
            .with_states(states4)
            .with_id(eid))


def marker_states(i, n=LEDS):
    states = [(0, 0, 3, 0)] * n
    states[i % n] = (0, 0, 100, 0)
    return states


def aggregate(colors, num_bands):
    """50 per-LED HSV colors -> num_bands averaged colors (circular hue)."""
    seg = band_segments(num_bands)
    out = []
    start = 0
    for end in seg:
        grp = colors[start:end + 1] or [colors[min(start, len(colors) - 1)]]
        sx = sum(math.cos(math.radians(h)) for h, s, v in grp)
        sy = sum(math.sin(math.radians(h)) for h, s, v in grp)
        h = int(math.degrees(math.atan2(sy, sx)) % 360)
        s = int(sum(s for _, s, _ in grp) / len(grp))
        v = int(sum(v for _, _, v in grp) / len(grp))
        out.append((h, max(1, s), v))
        start = end + 1
    return out


# ----------------------------- perimeter map -----------------------------
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


def grab_colors(sct, monitor, rects, stride, sat_boost, min_value, min_sat):
    frame = np.asarray(sct.grab(monitor))[:, :, :3][:, :, ::-1].astype(np.float32) / 255.0
    out = []
    for (x0, y0, x1, y1) in rects:
        region = frame[y0:y1:stride, x0:x1:stride]
        if region.size == 0:
            out.append((0, 1, min_value))
        else:
            out.append(extract_hsv(region.reshape(-1, 3), sat_boost, min_value, min_sat))
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


# ------------------------------- Engine ----------------------------------
class Engine:
    """Owns a background asyncio loop + device connection."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.device = None
        self._stop = threading.Event()
        self.status = {
            "connected": False, "syncing": False, "calibrating": False,
            "fps": 0.0, "send_ms": 0.0, "error": None,
        }

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---- connection ----
    async def _connect(self, cfg):
        self.device = await ApiClient(cfg["username"], cfg["password"]).l930(cfg["ip"])
        await self.device.on()

    def connect(self, cfg, on_done=None):
        def worker():
            try:
                self._submit(self._connect(cfg)).result(timeout=20)
                self.status["connected"] = True
                self.status["error"] = None
            except Exception as e:
                self.status["connected"] = False
                self.status["error"] = str(e)
            if on_done:
                on_done(self.status["connected"], self.status["error"])
        threading.Thread(target=worker, daemon=True).start()

    # ---- calibration marker ----
    def set_marker(self, i, brightness):
        if self.device is None:
            return
        self._submit(self.device.set_segment_effect(
            seg_painting(marker_states(i), int(brightness))))

    def solid(self, hue, sat):
        if self.device is None:
            return
        self._submit(self.device.set_hue_saturation(int(hue), int(sat)))

    # ---- sync loop ----
    def start_sync(self, cfg):
        if self.status["syncing"]:
            return
        self._stop.clear()
        self.status["syncing"] = True
        self._submit(self._sync_loop(dict(cfg)))

    def stop_sync(self):
        self._stop.set()

    async def _sync_loop(self, cfg):
        try:
            if self.device is None:
                await self._connect(cfg)
                self.status["connected"] = True
            brightness = int(cfg["brightness"]); interval = 1.0 / float(cfg["target_fps"])
            alpha = float(cfg["smoothing"]); stride = int(cfg["downsample_stride"])
            sat_boost = float(cfg["saturation_boost"]); min_value = int(cfg["min_value"])
            min_sat = int(cfg.get("min_sat", 5)); band_frac = float(cfg.get("band_frac", 0.15))
            min_change = int(cfg.get("min_change", 4)); num_bands = min(int(cfg.get("num_bands", 25)), LEDS)
            last_seg = int(cfg.get("last_segment", LEDS - 1))
            with mss.mss() as sct:
                monitor = sct.monitors[int(cfg["display_index"])]
                W, H = monitor["width"], monitor["height"]
                rects = build_map(LEDS, last_seg, W, H, cfg["corners"], band_frac)
                prev = last = None; sent = 0; send_acc = 0.0
                t_report = self.loop.time()
                while not self._stop.is_set():
                    t0 = self.loop.time()
                    seq = smooth(prev, grab_colors(sct, monitor, rects, stride, sat_boost, min_value, min_sat), alpha)
                    prev = seq
                    if changed_enough(last, seq, min_change):
                        states4 = [(h, s, v, 0) for (h, s, v) in aggregate(seq, num_bands)]
                        ts = self.loop.time()
                        await self.device.set_segment_effect(seg_painting(states4, brightness))
                        send_acc += self.loop.time() - ts
                        last = seq; sent += 1
                    now = self.loop.time()
                    if now - t_report >= 1.0:
                        self.status["fps"] = sent / (now - t_report)
                        self.status["send_ms"] = 1000 * send_acc / max(1, sent)
                        sent = 0; send_acc = 0.0; t_report = now
                    dt = now - t0
                    if dt < interval:
                        await asyncio.sleep(interval - dt)
        except Exception as e:
            self.status["error"] = str(e)
            self.status["connected"] = False
        finally:
            self.status["syncing"] = False
            self.status["fps"] = 0.0
            try:
                await self.device.set_hue_saturation(30, 40)
            except Exception:
                pass
