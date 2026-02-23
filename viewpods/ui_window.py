"""
Premium ViewPods UI — Final Custom Asset Renderer
Rewritten with PySide6 for 60FPS animations, real drop shadows, and pixel-perfect rendering.
"""

from __future__ import annotations

import logging
import os
import math
from typing import Callable, Optional
import ctypes

from PySide6.QtCore import (
    Qt, QRectF, QPropertyAnimation, QEasingCurve,
    QPointF, Signal, Slot, QTimer, QObject, QVariantAnimation
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QBrush,
    QPixmap, QImage, QFontDatabase, QFont, QLinearGradient
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QStackedLayout, QGraphicsOpacityEffect
)
from PySide6.QtSvg import QSvgRenderer

from viewpods.state_manager import ConnectionState, DeviceState

logger = logging.getLogger(__name__)

# ── Exact iOS Widget Tokens ──────────────────────────────────────────────
BG_PRIMARY = QColor(242, 242, 247)       # iOS Settings Light Background (#F2F2F7)
CARD_BG = QColor(255, 255, 255, 180)     # Translucent white for cards
CARD_BORDER = QColor(255, 255, 255, 255) # 1px highlight border
TRACK_BG = QColor(229, 229, 234)         # #E5E5EA empty ring
TEXT_COLOR_PRIMARY = QColor(0, 0, 0)
TEXT_COLOR_SECONDARY = QColor(142, 142, 147) # #8E8E93

# System Fonts (SF Pro style)
FONT_FAMILY = "Segoe UI Variable Display"  # Best native fallback on Win11 for SF Pro

WINDOW_WIDTH = 480
EXPANDED_HEIGHT = 220
COMPACT_HEIGHT = 175
RING_SIZE = 100
CARD_WIDTH = 130
CARD_EXPANDED_HEIGHT = 150
CARD_COMPACT_HEIGHT = 130
CORNER_RADIUS = 16

def apply_windows_11_mica(hwnd: int) -> None:
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        
        # 0 sets MICA to Light Mode background
        val = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(val), ctypes.sizeof(val)
        )
        backdrop = ctypes.c_int(2)  # Mica
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
    except Exception:
        pass


class ValueLabel(QLabel):
    """Animates numerical text changes smoothly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY.name()}; font-weight: bold; font-size: 20px;")
        
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(400)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._on_value_changed)
        
        self._current_val = -1
        
    def set_value(self, val: int, text: str = ""):
        if self._current_val == val:
            return
        if self._current_val == -1:
            self._current_val = val
            self.setText(f"{val}%" if not text else text)
            return
            
        self.anim.stop()
        self.anim.setStartValue(self._current_val)
        self.anim.setEndValue(val)
        self.anim.start()
        self._current_val = val
        self._custom_text = text
        
    def _on_value_changed(self, value: int):
        # We only animate to the number
        if hasattr(self, '_custom_text') and self._custom_text:
            if value == self._current_val:
                self.setText(self._custom_text)
            else:
                self.setText(f"{value}%")
        else:
            self.setText(f"{value}%")
            
        self.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY.name()}; font-weight: bold; font-size: 20px;")
            
    def set_empty(self, text: str = "—"):
        self._current_val = -1
        self.setText(text)
        if text != "—": # Longer text like "Open Case"
            self.setStyleSheet(f"color: {TEXT_COLOR_SECONDARY.name()}; font-weight: bold; font-size: 11px;")
        else:
            self.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY.name()}; font-weight: bold; font-size: 20px;")


class AnimatedBatteryRing(QWidget):
    """
    Renders a mathematically perfect, antialiased battery ring in C++.
    Animated progress and dynamic gradients based on Apple colors.
    """
    def __init__(self, filename: str, asset_type: str, crop_rule: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setFixedSize(RING_SIZE, RING_SIZE)
        
        self.current_value = 0.0
        self.target_value = 0.0
        self.is_charging = False
        self.crop_rule = crop_rule
        
        # Load icon into pixmap
        self.icon_pixmap = self._load_and_crop(filename, asset_type, crop_rule)
        
        # Animation for progress value
        self.progress_anim = QVariantAnimation(self)
        self.progress_anim.setDuration(600)
        self.progress_anim.setEasingCurve(QEasingCurve.OutQuart)
        self.progress_anim.valueChanged.connect(self._on_progress_changed)
        
        self.charging_rotation = 0
        self.charging_timer = QTimer(self)
        self.charging_timer.setInterval(32) # ~30fps for the subtle rotation
        self.charging_timer.timeout.connect(self._rotate_charging)

    def _load_and_crop(self, filename: str, asset_type: str, crop_rule: Optional[str]) -> QPixmap:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", filename)
        if not os.path.exists(path):
            return QPixmap()
            
        if asset_type == "SVG":
            # Case ratio
            icon_size = int(RING_SIZE * 0.48)
            renderer = QSvgRenderer(path)
            
            # Render directly to target scale bounds to prevent off-center clipping
            img = QImage(icon_size, icon_size, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            with QPainter(img) as painter:
                painter.setRenderHint(QPainter.Antialiasing)
                renderer.render(painter, QRectF(0, 0, icon_size, icon_size))
            
            # Convert SVG to black pixels for light mode
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    if c.alpha() > 0:
                        c.setRgb(0, 0, 0, min(255, int(c.alpha() * 1.5)))
                        img.setPixelColor(x, y, c)
            pixmap = QPixmap.fromImage(img)
            
        else:
            # Padded PNG ratio (massively inflate to counteract empty padding)
            icon_size = int(RING_SIZE * 1.85)
            pixmap = QPixmap(path)
            
            if crop_rule == "R":
                pixmap = pixmap.copy(0, 0, pixmap.width() // 2, pixmap.height())
            elif crop_rule == "L":
                pixmap = pixmap.copy(pixmap.width() // 2, 0, pixmap.width() // 2, pixmap.height())
                
            # Scale down smoothly
            pixmap = pixmap.scaled(icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
        return pixmap

    def set_value(self, value: Optional[int], charging: bool = False):
        if value is None:
            self.target_value = 0.0
            self.current_value = 0.0
            self.is_charging = False
            self.charging_timer.stop()
            self.update()
            return

        self.is_charging = charging
        if charging and not self.charging_timer.isActive():
            self.charging_timer.start()
        elif not charging:
            self.charging_timer.stop()

        if self.target_value != value:
            self.progress_anim.stop()
            self.progress_anim.setStartValue(self.current_value)
            self.progress_anim.setEndValue(float(value))
            self.progress_anim.start()
            self.target_value = value
        
        self.update()

    def _on_progress_changed(self, val):
        self.current_value = val
        self.update()

    def _rotate_charging(self):
        self.charging_rotation = (self.charging_rotation + 4) % 360
        self.update()

    def _get_color_for_value(self, val: float) -> QColor:
        if self.is_charging:
            return QColor(50, 215, 75) # Apple Green
        if val <= 20:
            return QColor(255, 69, 58) # Apple Red
        elif val <= 49:
            return QColor(255, 214, 10) # Apple Yellow
        return QColor(50, 215, 75)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            
            stroke_width = 7.0
            rect = QRectF(stroke_width, stroke_width, 
                         self.width() - stroke_width * 2, 
                         self.height() - stroke_width * 2)
    
            # Draw Track
            painter.setPen(QPen(TRACK_BG, stroke_width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 0, 360 * 16)
        
            if self.target_value > 0 or self.current_value > 0:
                color = self._get_color_for_value(self.current_value)
                
                # Subtle gradient for the ring
                grad = QLinearGradient(0, 0, self.width(), self.height())
                grad.setColorAt(0, color.lighter(110))
                grad.setColorAt(1, color.darker(110))
                
                painter.setPen(QPen(QBrush(grad), stroke_width, Qt.SolidLine, Qt.RoundCap))
                span_angle = int((self.current_value / 100.0) * 360 * 16)
                
                # Apple starts from top (90 degrees, but Qt goes CCW so 90 is top)
                start_angle = 90 * 16
                # Qt draws CCW for positive. We want CW.
                painter.drawArc(rect, start_angle, -span_angle)
                
                if self.is_charging:
                    # Pulsing arc overlay
                    pulse = abs(math.sin(self.charging_rotation * math.pi / 180)) * 100
                    glow_color = QColor(color)
                    glow_color.setAlpha(int(100 + pulse))
                    painter.setPen(QPen(glow_color, stroke_width, Qt.SolidLine, Qt.RoundCap))
                    painter.drawArc(rect, (90 - self.charging_rotation) * 16, - span_angle)
                    
                    # Draw small lightning bolt
                    bolt_w, bolt_h = 8, 14
                    bx = self.width() / 2
                    by = stroke_width
                    
                    painter.setPen(Qt.NoPen)
                    # Erase track behind bolt
                    painter.setBrush(BG_PRIMARY)
                    painter.drawEllipse(QPointF(bx, by), stroke_width*1.2, stroke_width*1.2)
                    
                    # Bolt path
                    path = QPainterPath()
                    path.moveTo(bx + bolt_w/3, by - bolt_h/2)
                    path.lineTo(bx - bolt_w/2, by + bolt_h/6)
                    path.lineTo(bx, by + bolt_h/6)
                    path.lineTo(bx - bolt_w/3, by + bolt_h/2)
                    path.lineTo(bx + bolt_w/2, by - bolt_h/6)
                    path.lineTo(bx, by - bolt_h/6)
                    path.closeSubpath()
                    painter.setBrush(color)
                    painter.drawPath(path)
    
            # Draw Icon in center
            if not self.icon_pixmap.isNull():
                iy = int((self.height() - self.icon_pixmap.height()) / 2)
                
                # Mathematical center correction for asymmetric asset padding
                if self.crop_rule == "L":
                    # Content center is exactly at 17.38% of the image width
                    center_ratio = 44.5 / 256.0
                    ix = int(self.width() / 2.0 - (center_ratio * self.icon_pixmap.width()))
                elif self.crop_rule == "R":
                    # Content center is exactly at 80.27% of the image width
                    center_ratio = 205.5 / 256.0
                    ix = int(self.width() / 2.0 - (center_ratio * self.icon_pixmap.width()))
                else:
                    ix = int((self.width() - self.icon_pixmap.width()) / 2)
                
                # If completely disconnected, desaturate/fade
                if self.target_value == 0 and not self.is_charging:
                    painter.setOpacity(0.4)
                    
                painter.drawPixmap(ix, iy, self.icon_pixmap)


class GlassCard(QWidget):
    """Wrapper for each Pod providing iOS Card aesthetic & hover effects."""
    def __init__(self, title: str, filename: str, asset_type: str, crop_rule: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setFixedSize(CARD_WIDTH, CARD_EXPANDED_HEIGHT)
        self.is_compact = False
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 15, 10, 10)
        self.layout.setAlignment(Qt.AlignCenter)
        self.layout.setSpacing(8)

        # Ring
        self.ring = AnimatedBatteryRing(filename, asset_type, crop_rule, self)
        self.layout.addWidget(self.ring, 0, Qt.AlignHCenter)
        
        # Percentage Label
        self.val_label = ValueLabel(self)
        self.layout.addWidget(self.val_label, 0, Qt.AlignHCenter)

        # Drop shadow
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QColor(0, 0, 0, 15))
        self.shadow.setOffset(0, 4)
        self.setGraphicsEffect(self.shadow)
        
        # Hover animation
        self.hover_anim = QVariantAnimation(self)
        self.hover_anim.setDuration(200)
        self.hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hover_anim.valueChanged.connect(self._on_hover_animate)
        
        # Height animation for compact mode
        self.height_anim = QPropertyAnimation(self, b"minimumHeight")
        self.height_anim.setDuration(300)
        self.height_anim.setEasingCurve(QEasingCurve.InOutQuart)
        
        self.scale_factor = 1.0

    def set_compact(self, compact: bool):
        if self.is_compact == compact: return
        self.is_compact = compact
        self.height_anim.stop()
        self.height_anim.setStartValue(self.height())
        self.height_anim.setEndValue(CARD_COMPACT_HEIGHT if compact else CARD_EXPANDED_HEIGHT)
        self.height_anim.start()
        
        if compact:
            self.val_label.hide()
        else:
            self.val_label.show()
        
    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Translate to center to scale properly
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            painter.translate(cx, cy)
            painter.scale(self.scale_factor, self.scale_factor)
            painter.translate(-cx, -cy)
            
            rect = QRectF(0, 0, self.width(), self.height()).adjusted(1, 1, -1, -1)
        
            # Draw translucent background
            painter.setBrush(CARD_BG)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
            
            # Draw 1px inner highlight border
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(CARD_BORDER, 1))
            painter.drawRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)

    def enterEvent(self, event):
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self.scale_factor)
        self.hover_anim.setEndValue(1.02)
        self.hover_anim.start()
        
        self.shadow.setBlurRadius(25)
        self.shadow.setColor(QColor(0, 0, 0, 25))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self.scale_factor)
        self.hover_anim.setEndValue(1.0)
        self.hover_anim.start()
        
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QColor(0, 0, 0, 15))
        super().leaveEvent(event)

    def _on_hover_animate(self, val):
        self.scale_factor = val
        self.update()
        
    def set_data(self, val: Optional[int], charging: bool, empty_text: str = "—"):
        self.ring.set_value(val, charging)
        if val is None:
            self.val_label.set_empty(empty_text)
        else:
            if val == 100:
                self.val_label.set_value(val, "Full")
            else:
                self.val_label.set_value(val)


class StatusWindow(QWidget):
    """
    Main UI Window replacing CustomTkinter.
    Public API remains identical to the old ui_window.py.
    """
    class UIUpdater(QObject):
        # Bridge thread-safe state updates to the main GUI thread
        state_updated = Signal(object)
        
    def __init__(self) -> None:
        # Avoid creating QApplication twice since we use __init__ on thread 1
        self._app: Optional[QApplication] = None
        self._initialized = False
        self.on_close: Optional[Callable[[], None]] = None
        self._updater = self.UIUpdater()
        
        # References
        self._left_card: Optional[GlassCard] = None
        self._right_card: Optional[GlassCard] = None
        self._case_card: Optional[GlassCard] = None
        self._status_label: Optional[QLabel] = None
        
        self.is_compact = False

    def initialize(self) -> None:
        if self._initialized: return
        
        # Qt requires QApplication before widgets
        if not QApplication.instance():
            self._app = QApplication([])
            
        super().__init__()
        self.setWindowTitle("ViewPods")
        self.setFixedSize(WINDOW_WIDTH, EXPANDED_HEIGHT)
        
        # Win11 settings / transparent background for Mica
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet(f"background-color: transparent;")
        
        # Connect cross-thread signal
        self._updater.state_updated.connect(self._apply_state)
        
        self._build_ui()
        self.center()
        
        self._setup_launch_animation()
        self._initialized = True

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Optional: Add a subtle overlay so elements pop against the wallpaper
        bg_widget = QWidget(self)
        bg_widget.setStyleSheet(f"background-color: {BG_PRIMARY.name()}; border-radius: 12px;")
        
        # We need absolute positioning or layouts since it's the backdrop
        bg_widget.setFixedSize(WINDOW_WIDTH, EXPANDED_HEIGHT)
        bg_layout = QVBoxLayout(bg_widget)
        bg_layout.setContentsMargins(20, 20, 20, 10)
        main_layout.addWidget(bg_widget)
        
        # Top bar for Toggle Button
        from PySide6.QtWidgets import QPushButton
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        self.toggle_btn = QPushButton("Collapse")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setStyleSheet(f"color: {TEXT_COLOR_SECONDARY.name()}; background: transparent; border: none; font-size: 11px;")
        self.toggle_btn.clicked.connect(self._toggle_mode)
        top_bar.addStretch()
        top_bar.addWidget(self.toggle_btn)
        
        bg_layout.addLayout(top_bar)
        
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(15)
        cards_layout.setAlignment(Qt.AlignCenter)
        
        # Left (R side of img image)
        self._left_card = GlassCard("Left", "AIRPODS_FINAL.png", "PNG", crop_rule="L")
        # Right (L side of img)
        self._right_card = GlassCard("Right", "AIRPODS_FINAL.png", "PNG", crop_rule="R")
        # Case
        self._case_card = GlassCard("Case", "case.svg", "SVG")

        cards_layout.addWidget(self._left_card)
        cards_layout.addWidget(self._right_card)
        cards_layout.addWidget(self._case_card)
        
        bg_layout.addLayout(cards_layout)
        
        self._status_label = QLabel("Looking for AirPods...")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet(f"color: {TEXT_COLOR_SECONDARY.name()}; font-family: '{FONT_FAMILY}'; font-size: 13px;")
        bg_layout.addWidget(self._status_label, 0, Qt.AlignBottom)
        
        self.bg_widget = bg_widget
        
        # Animation for window height
        self.window_anim = QPropertyAnimation(self, b"minimumHeight")
        self.window_anim.setDuration(300)
        self.window_anim.setEasingCurve(QEasingCurve.InOutQuart)
        
        self.bg_anim = QPropertyAnimation(self.bg_widget, b"minimumHeight")
        self.bg_anim.setDuration(300)
        self.bg_anim.setEasingCurve(QEasingCurve.InOutQuart)
        
        # Keep handle for Windows 11 Mica
        apply_windows_11_mica(int(self.winId()))
        
    def _toggle_mode(self):
        self.is_compact = not self.is_compact
        self._left_card.set_compact(self.is_compact)
        self._right_card.set_compact(self.is_compact)
        self._case_card.set_compact(self.is_compact)
        
        target_h = COMPACT_HEIGHT if self.is_compact else EXPANDED_HEIGHT
        
        self.window_anim.stop()
        self.window_anim.setStartValue(self.height())
        self.window_anim.setEndValue(target_h)
        self.window_anim.start()
        
        self.bg_anim.stop()
        self.bg_anim.setStartValue(self.bg_widget.height())
        self.bg_anim.setEndValue(target_h)
        self.bg_anim.start()
        
        if self.is_compact:
            self._status_label.hide()
            self.toggle_btn.setText("Expand")
        else:
            self._status_label.show()
            self.toggle_btn.setText("Collapse")
        
        # We also need to set maximumHeight to allow shrinking
        self.setMaximumHeight(target_h)
        self.bg_widget.setMaximumHeight(target_h)

    def _setup_launch_animation(self):
        self.launch_anim = QPropertyAnimation(self, b"windowOpacity")
        self.launch_anim.setDuration(400)
        self.launch_anim.setStartValue(0.0)
        self.launch_anim.setEndValue(1.0)
        self.launch_anim.setEasingCurve(QEasingCurve.InOutQuad)

    def center(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    def closeEvent(self, event):
        if self.on_close:
            self.on_close()
        event.accept()

    def run(self) -> None:
        self.show()
        self.launch_anim.start()
        if self._app:
            self._app.exec()

    def destroy(self) -> None:
        if self._initialized:
            self.close()
            # QApplication quit is usually handled by last window closed,
            # or by sys.exit(), but we can enforce it.
            if self._app:
                self._app.quit()
        self._initialized = False

    def update_state(self, state: DeviceState) -> None:
        # Thread safe emission
        self._updater.state_updated.emit(state)

    @Slot(object)
    def _apply_state(self, state: DeviceState) -> None:
        if not self._initialized: return
        
        if not state.bluetooth_available:
            self._status_label.setText("Bluetooth is off")
            self._zero_out()
        elif state.is_connected and state.airpods:
            self._status_label.setText(f"Connected: {state.airpods.model}")
            a = state.airpods
            self._left_card.set_data(a.left_battery, a.left_charging)
            self._right_card.set_data(a.right_battery, a.right_charging)
            self._case_card.set_data(a.case_battery, a.case_charging, empty_text="Open Case")
            
            # Smart status
            if state.is_low_battery:
                self._status_label.setText("Low Battery")
            elif a.left_battery == 100 and a.right_battery == 100:
                self._status_label.setText("Fully Charged")
            
        elif state.is_connected and state.classic_device_name:
            self._status_label.setText(f"Connected: {state.classic_device_name}")
            self._zero_out()
        else:
            self._status_label.setText("Looking for AirPods...")
            self._zero_out()

    def _zero_out(self):
        self._left_card.set_data(None, False)
        self._right_card.set_data(None, False)
        self._case_card.set_data(None, False, empty_text="Open Case")

