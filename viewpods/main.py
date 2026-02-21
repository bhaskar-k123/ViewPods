"""
ViewPods — Main application entry point.

Orchestrates all modules: BLE scanner, state manager, and status window.
Handles graceful startup and shutdown sequencing.
"""

from __future__ import annotations

import logging
import signal
import sys

from viewpods.ble_scanner import BLEScanner
from viewpods.bt_device_checker import BtDeviceChecker
from viewpods.state_manager import StateManager
from viewpods.ui_window import StatusWindow

# ── Logging Setup ────────────────────────────────────────────────────────
# Minimal logging in production; set DEBUG via environment variable
LOG_LEVEL = logging.WARNING

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("viewpods")


def main() -> None:
    """Launch the ViewPods battery monitor."""
    logger.info("ViewPods starting…")

    # ── 1. State Manager ─────────────────────────────────────────────
    state_manager = StateManager()
    state_manager.start_timeout_checker()

    # ── 2. BLE Scanner (detects battery data from proximity pairing) ─
    scanner = BLEScanner(state_manager)
    scanner.start()

    # ── 3. BT Classic Checker (detects connected AirPods via Windows) ─
    bt_checker = BtDeviceChecker()
    bt_checker.start(
        on_connected=state_manager.mark_connected_classic,
        on_disconnected=state_manager.mark_classic_disconnected,
    )

    # ── 4. Status Window (main thread) ───────────────────────────────
    window = StatusWindow()
    window.initialize()
    state_manager.add_observer(window.update_state)

    def _shutdown():
        logger.info("Shutting down…")
        bt_checker.stop()
        try:
            scanner.stop()
        except RuntimeError as e:
            if "Event loop is closed" not in str(e):
                raise
        state_manager.stop()
        window.destroy()

    window.on_close = _shutdown

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: _shutdown())

    try:
        # This blocks until the window is closed
        window.run()
    except KeyboardInterrupt:
        _shutdown()
    finally:
        logger.info("ViewPods stopped.")


if __name__ == "__main__":
    main()
