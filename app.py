"""
tapo_ambilight_app.py  --  Tapo L930 Ambilight (desktop app)
============================================================
A small Windows desktop app around the no-blink per-zone screen-sync engine.

Features
  - Settings window (connection + lighting sliders) -- no JSON editing
  - One-click Start / Stop with live status (fps, send latency)
  - Guided Calibration Wizard (tag the 4 screen corners + strip end)
  - Minimize to system tray; run in the background
  - Optional "Start with Windows"

Install:  pip install tapo mss numpy pystray pillow
Run:      pythonw tapo_ambilight_app.py     (or double-click run.bat)
"""

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from engine import Engine, LEDS

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
APP_NAME = "TapoAmbilight"

DEFAULTS = {
    "username": "", "password": "", "ip": "",
    "brightness": 80, "saturation_boost": 1.8, "min_value": 10, "min_sat": 30,
    "display_index": 0, "reverse": False,
    "target_fps": 12, "smoothing": 0.55, "downsample_stride": 8,
    "min_change": 4, "band_frac": 0.15, "num_bands": 25,
}

# optional deps
try:
    import pystray
    from PIL import Image, ImageDraw
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False

try:
    import winreg
    HAVE_WINREG = True
except Exception:
    HAVE_WINREG = False


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ----------------------------- autostart ---------------------------------
def _run_key():
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run",
                          0, winreg.KEY_ALL_ACCESS)


def autostart_enabled():
    if not HAVE_WINREG:
        return False
    try:
        with _run_key() as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except Exception:
        return False


def set_autostart(enable):
    if not HAVE_WINREG:
        return
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    target = f'"{pyw}" "{os.path.join(HERE, os.path.basename(__file__))}" --tray'
    try:
        with _run_key() as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


# ----------------------------- calibration wizard ------------------------
class CalibrationWizard(tk.Toplevel):
    def __init__(self, master, engine, cfg):
        super().__init__(master)
        self.engine = engine
        self.cfg = cfg
        self.title("Calibration Wizard")
        self.resizable(False, False)
        self.i = 0
        self.corners = dict(cfg.get("corners", {}))
        self.last_segment = int(cfg.get("last_segment", LEDS - 1))

        ttk.Label(self, text="A single white LED is lit on your strip.\n"
                             "Step it around the loop and tag each screen corner.",
                  justify="center").pack(padx=16, pady=(14, 8))

        self.seglbl = ttk.Label(self, text="", font=("Segoe UI", 18, "bold"))
        self.seglbl.pack(pady=4)

        nav = ttk.Frame(self); nav.pack(pady=6)
        ttk.Button(nav, text="◀ Prev", command=self.prev).grid(row=0, column=0, padx=4)
        self.jump = ttk.Spinbox(nav, from_=0, to=LEDS - 1, width=5)
        self.jump.grid(row=0, column=1, padx=4)
        ttk.Button(nav, text="Go", command=self.go).grid(row=0, column=2, padx=4)
        ttk.Button(nav, text="Next ▶", command=self.next).grid(row=0, column=3, padx=4)

        tags = ttk.LabelFrame(self, text="Tag the CURRENT lit LED as:")
        tags.pack(padx=16, pady=10, fill="x")
        grid = ttk.Frame(tags); grid.pack(pady=6)
        ttk.Button(grid, text="Top-Left", command=lambda: self.tag("TL")).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(grid, text="Top-Right", command=lambda: self.tag("TR")).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(grid, text="Bottom-Left", command=lambda: self.tag("BL")).grid(row=1, column=0, padx=4, pady=4)
        ttk.Button(grid, text="Bottom-Right", command=lambda: self.tag("BR")).grid(row=1, column=1, padx=4, pady=4)
        ttk.Button(grid, text="Mark strip END", command=self.tag_end).grid(row=2, column=0, columnspan=2, pady=4)

        self.tagslbl = ttk.Label(self, text="", justify="center")
        self.tagslbl.pack(pady=4)

        bottom = ttk.Frame(self); bottom.pack(pady=(6, 14))
        ttk.Button(bottom, text="Save & Close", command=self.save).grid(row=0, column=0, padx=6)
        ttk.Button(bottom, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=6)

        self.refresh()
        self.transient(master)
        self.grab_set()

    def show_marker(self):
        self.engine.set_marker(self.i, self.cfg.get("brightness", 80))

    def refresh(self):
        self.seglbl.config(text=f"LED  {self.i} / {LEDS - 1}")
        self.tagslbl.config(text=f"corners = {self.corners}    end = {self.last_segment}")
        self.show_marker()

    def next(self):
        self.i = (self.i + 1) % LEDS; self.refresh()

    def prev(self):
        self.i = (self.i - 1) % LEDS; self.refresh()

    def go(self):
        try:
            self.i = int(self.jump.get()) % LEDS
        except Exception:
            return
        self.refresh()

    def tag(self, name):
        self.corners[name] = self.i; self.refresh()

    def tag_end(self):
        self.last_segment = self.i; self.refresh()

    def save(self):
        if not all(k in self.corners for k in ("TR", "TL", "BL", "BR")):
            messagebox.showwarning("Calibration", "Tag all four corners first (TL, TR, BL, BR).")
            return
        self.cfg["corners"] = self.corners
        self.cfg["last_segment"] = self.last_segment
        save_config(self.cfg)
        messagebox.showinfo("Calibration", "Saved. You can now Start the sync.")
        self.destroy()


# ----------------------------- main app ----------------------------------
class App:
    def __init__(self, start_in_tray=False):
        self.cfg = load_config()
        self.engine = Engine()
        self.tray = None

        self.root = tk.Tk()
        self.root.title("Tapo Ambilight")
        self.root.resizable(False, False)
        try:
            self.root.iconphoto(False, tk.PhotoImage(width=1, height=1))
        except Exception:
            pass

        self.vars = {}
        self._build_ui()
        self._load_into_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray if HAVE_TRAY else self.quit)
        self._poll_status()

        if HAVE_TRAY:
            self._start_tray()
        if start_in_tray:
            self.root.after(200, self.hide_to_tray)
            self.root.after(600, lambda: self.toggle_sync(force_start=True))

    # ---- UI ----
    def _build_ui(self):
        pad = dict(padx=10, pady=4)
        top = ttk.Frame(self.root); top.pack(fill="x", padx=10, pady=(10, 0))
        self.status_dot = tk.Canvas(top, width=14, height=14, highlightthickness=0)
        self.status_dot.grid(row=0, column=0, padx=(0, 6))
        self.status_lbl = ttk.Label(top, text="Disconnected", font=("Segoe UI", 10, "bold"))
        self.status_lbl.grid(row=0, column=1, sticky="w")
        self.fps_lbl = ttk.Label(top, text="")
        self.fps_lbl.grid(row=0, column=2, sticky="e", padx=(20, 0))

        nb = ttk.Notebook(self.root); nb.pack(fill="both", expand=True, padx=10, pady=8)

        # Connection
        conn = ttk.Frame(nb); nb.add(conn, text="Connection")
        self._entry(conn, "Tapo email", "username", 0)
        self._entry(conn, "Tapo password", "password", 1, show="•")
        self._entry(conn, "Strip IP", "ip", 2)
        ttk.Button(conn, text="Connect / Test", command=self.connect).grid(row=3, column=1, sticky="w", **pad)

        # Lighting
        light = ttk.Frame(nb); nb.add(light, text="Lighting")
        self._slider(light, "Brightness", "brightness", 1, 100, 0, int_=True)
        self._slider(light, "Saturation boost", "saturation_boost", 1.0, 3.0, 1)
        self._slider(light, "Bands (zones)", "num_bands", 4, 50, 2, int_=True)
        self._slider(light, "Target FPS", "target_fps", 1, 30, 3, int_=True)
        self._slider(light, "Smoothing", "smoothing", 0.0, 0.9, 4)
        self._slider(light, "Min change", "min_change", 0, 20, 5, int_=True)
        self._slider(light, "Min brightness floor", "min_value", 0, 50, 6, int_=True)
        self._slider(light, "Edge sample depth", "band_frac", 0.05, 0.35, 7)
        self._spin(light, "Monitor (display index)", "display_index", 0, 4, 8)
        self.vars["reverse"] = tk.BooleanVar()
        ttk.Checkbutton(light, text="Reverse direction", variable=self.vars["reverse"]).grid(
            row=9, column=1, sticky="w", **pad)

        # Calibration
        cal = ttk.Frame(nb); nb.add(cal, text="Calibration")
        ttk.Label(cal, text="Map your strip's physical loop to the screen corners.\n"
                            "Required once (or after re-routing the strip).",
                  justify="left").pack(anchor="w", padx=10, pady=(12, 6))
        self.cal_lbl = ttk.Label(cal, text="")
        self.cal_lbl.pack(anchor="w", padx=10, pady=4)
        ttk.Button(cal, text="Run Calibration Wizard", command=self.calibrate).pack(anchor="w", padx=10, pady=8)

        # bottom bar
        bar = ttk.Frame(self.root); bar.pack(fill="x", padx=10, pady=(0, 10))
        self.start_btn = ttk.Button(bar, text="▶  Start", command=self.toggle_sync)
        self.start_btn.grid(row=0, column=0, padx=(0, 8), ipadx=10, ipady=2)
        ttk.Button(bar, text="Save settings", command=self.save_ui).grid(row=0, column=1, padx=4)
        if HAVE_TRAY:
            ttk.Button(bar, text="Hide to tray", command=self.hide_to_tray).grid(row=0, column=2, padx=4)
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(bar, text="Start with Windows", variable=self.auto_var,
                        command=self.toggle_autostart).grid(row=0, column=3, padx=8)

    def _entry(self, parent, label, key, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=8, pady=6)
        v = tk.StringVar(); self.vars[key] = v
        ttk.Entry(parent, textvariable=v, width=34, show=show).grid(row=row, column=1, sticky="w", padx=8, pady=6)

    def _slider(self, parent, label, key, lo, hi, row, int_=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=8, pady=5)
        v = tk.DoubleVar(); self.vars[key] = v
        val = ttk.Label(parent, width=5)
        def upd(_=None):
            val.config(text=str(int(v.get()) if int_ else round(v.get(), 2)))
        s = ttk.Scale(parent, from_=lo, to=hi, variable=v, command=upd, length=240)
        s.grid(row=row, column=1, sticky="w", padx=8, pady=5)
        val.grid(row=row, column=2, sticky="w")
        self.vars[key + "__upd"] = upd

    def _spin(self, parent, label, key, lo, hi, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=8, pady=5)
        v = tk.IntVar(); self.vars[key] = v
        ttk.Spinbox(parent, from_=lo, to=hi, textvariable=v, width=6).grid(
            row=row, column=1, sticky="w", padx=8, pady=5)

    def _load_into_ui(self):
        for k, var in self.vars.items():
            if k.endswith("__upd") or k not in self.cfg:
                continue
            try:
                var.set(self.cfg[k])
            except Exception:
                pass
        for k, var in self.vars.items():
            if k.endswith("__upd"):
                var()
        self._refresh_cal_label()

    def _refresh_cal_label(self):
        c = self.cfg.get("corners")
        if c:
            self.cal_lbl.config(text=f"Calibrated  ✓   corners={c}  end={self.cfg.get('last_segment')}")
        else:
            self.cal_lbl.config(text="Not calibrated yet — run the wizard.")

    # ---- actions ----
    def collect_ui(self):
        for k, var in self.vars.items():
            if k.endswith("__upd"):
                continue
            val = var.get()
            if k in ("brightness", "num_bands", "target_fps", "min_change",
                     "min_value", "display_index"):
                val = int(round(float(val)))
            self.cfg[k] = val
        return self.cfg

    def save_ui(self):
        self.collect_ui(); save_config(self.cfg)
        messagebox.showinfo("Saved", "Settings saved.")

    def connect(self):
        self.collect_ui()
        self.status_lbl.config(text="Connecting…")
        self.engine.connect(self.cfg, on_done=lambda ok, err: self.root.after(0, self._conn_done, ok, err))

    def _conn_done(self, ok, err):
        if not ok:
            messagebox.showerror("Connection failed",
                                 f"{err}\n\nTip: in the Tapo app enable\nMe → Tapo Lab → Third-Party Compatibility.")

    def calibrate(self):
        self.collect_ui()
        if not self.cfg.get("ip"):
            messagebox.showwarning("Calibration", "Enter connection details and Connect first.")
            return
        def after_conn(ok, err):
            if ok:
                self.root.after(0, lambda: self._open_wizard())
            else:
                self.root.after(0, lambda: self._conn_done(ok, err))
        if self.engine.status["connected"]:
            self._open_wizard()
        else:
            self.status_lbl.config(text="Connecting…")
            self.engine.connect(self.cfg, on_done=after_conn)

    def _open_wizard(self):
        CalibrationWizard(self.root, self.engine, self.cfg)
        self.root.wait_window()
        self.cfg = load_config()
        self._refresh_cal_label()

    def toggle_sync(self, force_start=False):
        if self.engine.status["syncing"] and not force_start:
            self.engine.stop_sync()
            self.start_btn.config(text="▶  Start")
        else:
            self.collect_ui()
            if not self.cfg.get("corners"):
                messagebox.showwarning("Not calibrated", "Run the Calibration Wizard first.")
                return
            save_config(self.cfg)
            self.engine.start_sync(self.cfg)
            self.start_btn.config(text="■  Stop")

    def toggle_autostart(self):
        set_autostart(self.auto_var.get())

    # ---- status polling ----
    def _poll_status(self):
        st = self.engine.status
        if st["syncing"]:
            color, text = "#27c93f", f"Syncing"
            self.fps_lbl.config(text=f"{st['fps']:.1f} fps · {st['send_ms']:.0f} ms")
            self.start_btn.config(text="■  Stop")
        elif st["connected"]:
            color, text = "#2d8cff", "Connected"
            self.fps_lbl.config(text="")
            self.start_btn.config(text="▶  Start")
        else:
            color, text = "#ff5f56", "Disconnected"
            self.fps_lbl.config(text="")
        if st["error"]:
            text = "Error"
            color = "#ff5f56"
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 12, 12, fill=color, outline="")
        self.status_lbl.config(text=text)
        self.root.after(500, self._poll_status)

    # ---- tray ----
    def _icon_image(self):
        img = Image.new("RGB", (64, 64), "#101014")
        d = ImageDraw.Draw(img)
        for i, col in enumerate(["#ff5f56", "#ffbd2e", "#27c93f", "#2d8cff"]):
            d.rectangle([8, 8 + i * 12, 56, 16 + i * 12], fill=col)
        return img

    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open", self._tray_open, default=True),
            pystray.MenuItem("Start", lambda: self.root.after(0, lambda: self.toggle_sync(force_start=True))),
            pystray.MenuItem("Stop", lambda: self.root.after(0, self.engine.stop_sync)),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.tray = pystray.Icon(APP_NAME, self._icon_image(), "Tapo Ambilight", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_open(self, *a):
        self.root.after(0, self.show_window)

    def _tray_quit(self, *a):
        self.root.after(0, self.quit)

    def show_window(self):
        self.root.deiconify(); self.root.lift()

    def hide_to_tray(self):
        if HAVE_TRAY:
            self.root.withdraw()
        else:
            self.root.iconify()

    def quit(self):
        try:
            self.engine.stop_sync()
        except Exception:
            pass
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App(start_in_tray=("--tray" in sys.argv)).run()
