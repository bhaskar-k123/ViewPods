"""
Premium ViewPods UI — Final Custom Asset Renderer
Handles custom PNGs and SVGs perfectly, honoring exact crop mappings.
"""

from __future__ import annotations

import logging
import threading
import os
import io
from typing import Callable, Optional
import ctypes

from PIL import Image, ImageDraw
import fitz  # PyMuPDF for flawless SVG handling on Windows

import customtkinter as ctk

from viewpods.state_manager import ConnectionState, DeviceState

logger = logging.getLogger(__name__)

# ── Exact iOS Widget Tokens ──────────────────────────────────────────────
BG_PRIMARY = "#1C1C1E"       # Widget Dark Background
TRACK_BG = "#303030"         # The empty ring background
BAR_GREEN = "#32D74B"        # bright green
BAR_YELLOW = "#FFD60A"       # warning yellow
BAR_RED = "#FF453A"          # critical red

TEXT_COLOR_PRIMARY = "#FFFFFF"
TEXT_COLOR_SECONDARY = "#8E8E93"

FONT_FAMILY = "Segoe UI Variable Display"  

WINDOW_WIDTH = 480
WINDOW_HEIGHT = 220
RING_SIZE = 110


def apply_windows_11_mica(hwnd: int) -> None:
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(val), ctypes.sizeof(val)
        )
        backdrop = ctypes.c_int(2)  # Mica
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
    except Exception:
        pass


def load_and_crop_asset(filename: str, asset_type: str, target_size: int, crop_rule: Optional[str] = None) -> Optional[Image.Image]:
    """
    Loads and expertly crops the user's specific assets.
    AIRPODS_FINAL.png holds both buds. Left side is RIGHT bud. Right side is LEFT bud.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", filename)
    if not os.path.exists(path):
        logger.error(f"Asset missing: {path}")
        return None

    try:
        if asset_type == "SVG":
            # Render SVG via PyMuPDF at high resolution for maximum anti-aliasing
            doc = fitz.open(path)
            pix = doc[0].get_pixmap(alpha=True, matrix=fitz.Matrix(3, 3))
            
            # Load raw bytes into PIL
            raw_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
            
            # Make the SVG pixels white instead of black to match iOS dark mode icons
            r, g, b, a = raw_img.split()
            r = r.point(lambda _: 255)
            g = g.point(lambda _: 255)
            b = b.point(lambda _: 255)
            # Any anti-aliased edge pixel is amplified to increase stroke weight against the dark background
            a = a.point(lambda p: min(255, int(p * 2.5)))
            raw_img = Image.merge("RGBA", (r, g, b, a))
        else:
            # Standard PNG, no white override needed (usually shaded from photos/renders)
            raw_img = Image.open(path).convert("RGBA")

        # The user specifically noted:
        # "the left SIDE IS THE RIGHT AIRPOD AND THE RIGHT SIDE IS THE LEFT AIRPOD."
        if crop_rule == "R":
            # Right bud needs the LEFT half of the image
            raw_img = raw_img.crop((0, 0, raw_img.width // 2, raw_img.height))
        elif crop_rule == "L":
            # Left bud needs the RIGHT half of the image
            raw_img = raw_img.crop((raw_img.width // 2, 0, raw_img.width, raw_img.height))
            
        # Crop tight to the actual visible non-transparent pixels!
        bbox = raw_img.getbbox()
        if bbox:
            raw_img = raw_img.crop(bbox)
            
        # Downscale gracefully to perfectly fit inside the requested target size bounds
        ratio = min(target_size / raw_img.width, target_size / raw_img.height)
        new_size = (max(1, int(raw_img.width * ratio)), max(1, int(raw_img.height * ratio)))
        
        return raw_img.resize(new_size, Image.Resampling.LANCZOS)
        
    except Exception as e:
        logger.error(f"Failed to load user asset {filename}: {e}")
        return None


class AntiAliasedBatteryRing(ctk.CTkLabel):
    """
    Renders a mathematically perfect, anti-aliased battery ring
    by drawing it via PIL at 4x resolution, downscaling, and mapping it to a CTkImage.
    """
    def __init__(self, master, device_type: str, filename: str, asset_type: str, crop_rule: Optional[str] = None, size: int = RING_SIZE, **kwargs):
        super().__init__(master, text="", width=size, height=size, **kwargs)
        self.size = size
        self.device_type = device_type
        
        # Load the user's asset into memory cache using unified loader
        # The icon should take up roughly 45% of the total ring space
        icon_size = int(self.size * 0.45)
        self._asset_cache = load_and_crop_asset(filename, asset_type, icon_size, crop_rule)
            
        self.set(None)

    def set(self, value: Optional[int], charging: bool = False):
        scale = 4
        s_size = self.size * scale
        
        base = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)

        stroke_w = 6 * scale
        padding = 10 * scale
        
        bbox = [padding, padding, s_size - padding, s_size - padding]
        
        # Draw the empty track
        draw.arc(bbox, 0, 360, fill=TRACK_BG, width=stroke_w)
        
        if value is not None:
            color = BAR_GREEN
            if value <= 20: color = BAR_RED
            elif value <= 50: color = BAR_YELLOW
            if charging: color = BAR_GREEN 
            
            start_angle = -90
            extent = (value / 100.0) * 360
            end_angle = start_angle + extent
            
             # PIL drawing arcs exactly at 360 can bug out, stop just shy
            if extent > 359:
                end_angle = start_angle + 359.9
                
            if extent > 0:
                draw.arc(bbox, start_angle, end_angle, fill=color, width=stroke_w)
                
                # Apple UI Kit uses rounded caps for the progress ring!
                # We calculate the Cartesian coordinates for the start/end angles to draw circular caps.
                import math
                radius = (s_size - 2 * padding) / 2
                center_x = s_size / 2
                center_y = s_size / 2
                
                def get_point(angle_deg):
                    # convert to radians
                    rad = math.radians(angle_deg)
                    # For PIL arcs, 0 is at 3 o'clock, 90 is at 6 o'clock (y-axis goes down)
                    x = center_x + radius * math.cos(rad)
                    y = center_y + radius * math.sin(rad)
                    return (x, y)
                
                # Cap diameter matches stroke width closely
                cap_r = stroke_w / 2
                
                # Start cap
                sx, sy = get_point(start_angle)
                draw.ellipse([sx - cap_r, sy - cap_r, sx + cap_r, sy + cap_r], fill=color)
                
                # End cap
                ex, ey = get_point(end_angle)
                draw.ellipse([ex - cap_r, ey - cap_r, ex + cap_r, ey + cap_r], fill=color)
                
            if charging:
                bolt_x = s_size // 2
                bolt_y = padding
                bolt_r = 12 * scale
                
                # Erase the ring
                draw.ellipse([bolt_x - bolt_r, bolt_y - bolt_r, bolt_x + bolt_r, bolt_y + bolt_r], fill=BG_PRIMARY)
                
                # Lightning bolt
                b_w = 5 * scale
                b_h = 10 * scale
                bolt_poly = [
                    (bolt_x + b_w/3, bolt_y - b_h/2),
                    (bolt_x - b_w, bolt_y + b_h/6),
                    (bolt_x, bolt_y + b_h/6),
                    (bolt_x - b_w/3, bolt_y + b_h/2),
                    (bolt_x + b_w, bolt_y - b_h/6),
                    (bolt_x, bolt_y - b_h/6)
                ]
                draw.polygon(bolt_poly, fill=color)

        final_img = base.resize((self.size, self.size), Image.Resampling.LANCZOS)
        
        # Paste the user's asset directly into the center
        if self._asset_cache:
            cx = (self.size - self._asset_cache.width) // 2
            cy = (self.size - self._asset_cache.height) // 2
            # Use the alpha channel to make sure it blends perfectly over the background
            final_img.paste(self._asset_cache, (cx, cy), mask=self._asset_cache)
        else:
            from PIL import ImageFont
            draw_final = ImageDraw.Draw(final_img)
            draw_final.text((self.size/2, self.size/2), self.device_type, fill=TEXT_COLOR_PRIMARY, anchor="mm", font_size=20)

        ctk_img = ctk.CTkImage(light_image=final_img, dark_image=final_img, size=(self.size, self.size))
        
        self.configure(image=ctk_img)
        self._current_image = ctk_img


class StatusWindow:
    def __init__(self) -> None:
        self._root: Optional[ctk.CTk] = None
        self._lock = threading.Lock()
        self._current_state: Optional[DeviceState] = None
        self._initialized = False
        self.on_close: Optional[Callable[[], None]] = None

        self._left_ring: Optional[AntiAliasedBatteryRing] = None
        self._right_ring: Optional[AntiAliasedBatteryRing] = None
        self._case_ring: Optional[AntiAliasedBatteryRing] = None
        self._status_label: Optional[ctk.CTkLabel] = None

    def initialize(self) -> None:
        if self._initialized: return
        ctk.set_appearance_mode("dark")
        self._root = ctk.CTk()
        self._root.title("ViewPods")
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self._root.resizable(False, False)
        self._root.configure(fg_color=BG_PRIMARY)
        self._root.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            apply_windows_11_mica(hwnd)
        except Exception: pass

        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = (screen_w - WINDOW_WIDTH) // 2
        y = (screen_h - WINDOW_HEIGHT) // 2
        self._root.geometry(f"+{x}+{y}")
        self._root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self._build_ui()
        self._initialized = True

    def _handle_close(self) -> None:
        if self.on_close: self.on_close()
        else: self.destroy()

    def run(self) -> None:
        if self._root: self._root.mainloop()

    def destroy(self) -> None:
        if self._root:
            try:
                self._root.quit()
                self._root.destroy()
            except Exception: pass
            self._root = None
            self._initialized = False

    def _build_ui(self) -> None:
        gauge_frame = ctk.CTkFrame(self._root, fg_color="transparent")
        gauge_frame.pack(expand=True, fill="both", pady=25)

        layout = ctk.CTkFrame(gauge_frame, fg_color="transparent")
        layout.pack(anchor="center")

        # Apple's native widget has tight spacing. 15px pad x 2 sides = 30px per ring. 
        # Total: 110*3 + 30*3 = 420px. Easily fits inside 480px WINDOW_WIDTH without crushing.
        spacing = 15
        
        def build_column(parent, device_type, filename, asset_type, crop_rule=None):
            col = ctk.CTkFrame(parent, fg_color="transparent")
            ring = AntiAliasedBatteryRing(col, device_type, filename, asset_type, crop_rule)
            ring.pack()
            # Bolder UI font to match Apple UI Toolkit
            pct = ctk.CTkLabel(col, text="--%", font=(FONT_FAMILY, 28, "bold"), text_color=TEXT_COLOR_PRIMARY)
            pct.pack(pady=(10, 0))
            return col, ring, pct
            
        # UI Order: Left | Right | Case. 
        # AIRPODS_FINAL.png Mapping: Left side of image is 'R', Right side is 'L'
        l_col, self._left_ring, self._l_pct = build_column(layout, "L", "AIRPODS_FINAL.png", "PNG", crop_rule="L")
        l_col.pack(side="left", padx=spacing)

        r_col, self._right_ring, self._r_pct = build_column(layout, "R", "AIRPODS_FINAL.png", "PNG", crop_rule="R")
        r_col.pack(side="left", padx=spacing)
        
        # And use the case SVG for the case, passing through the color filter
        c_col, self._case_ring, self._c_pct = build_column(layout, "Case", "case.svg", "SVG")
        c_col.pack(side="left", padx=spacing)

        self._status_label = ctk.CTkLabel(self._root, text="Looking for AirPods...", font=(FONT_FAMILY, 14), text_color=TEXT_COLOR_SECONDARY)
        self._status_label.pack(side="bottom", pady=8)

    def update_state(self, state: DeviceState) -> None:
        self._current_state = state
        if self._root and self._initialized:
            try: self._root.after(0, self._apply_state, state)
            except Exception: pass

    def _apply_state(self, state: DeviceState) -> None:
        if not self._initialized: return
        if not state.bluetooth_available:
            self._status_label.configure(text="Bluetooth is off")
            self._zero_out()
        elif state.is_connected and state.airpods:
            self._status_label.configure(text=f"Connected: {state.airpods.model}")
            a = state.airpods
            self._left_ring.set(a.left_battery, a.left_charging)
            self._right_ring.set(a.right_battery, a.right_charging)
            self._case_ring.set(a.case_battery, a.case_charging)
            self._l_pct.configure(text=f"{a.left_battery}%" if a.left_battery is not None else "--%")
            self._r_pct.configure(text=f"{a.right_battery}%" if a.right_battery is not None else "--%")
            self._c_pct.configure(text=f"{a.case_battery}%" if a.case_battery is not None else "--%")
        elif state.is_connected and state.classic_device_name:
            self._status_label.configure(text=f"Connected: {state.classic_device_name}")
            self._zero_out()
        else:
            self._status_label.configure(text="Looking for AirPods...")
            self._zero_out()

    def _zero_out(self):
        self._left_ring.set(None)
        self._right_ring.set(None)
        self._case_ring.set(None)
        self._l_pct.configure(text="--%")
        self._r_pct.configure(text="--%")
        self._c_pct.configure(text="--%")
