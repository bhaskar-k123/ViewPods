"""
BLE scanner module — event-driven AirPods detection using bleak.

Runs an asyncio event loop in a dedicated daemon thread. Filters for
Apple manufacturer data and pushes parsed results to the state manager.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from viewpods.packet_parser import APPLE_COMPANY_ID, parse_manufacturer_data
from viewpods.state_manager import StateManager

logger = logging.getLogger(__name__)


class BLEScanner:
    """Asynchronous BLE scanner that detects AirPods advertisements.

    Runs its own asyncio event loop in a background daemon thread so
    the UI thread is never blocked.
    """

    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._scanner: Optional[BleakScanner] = None
        self._running = False
        self._stop_event = threading.Event()

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start scanning in a background thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="BLEScanner",
            daemon=True,
        )
        self._thread.start()
        logger.info("BLE scanner thread started")

    def stop(self) -> None:
        """Signal the scanner to stop and wait for the thread to exit."""
        self._running = False
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("BLE scanner thread stopped")

    # ── Background Thread ────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Entry point for the scanner thread — runs the asyncio loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._scan())
        except RuntimeError as exc:
            # Expected when the loop is explicitly closed during shutdown
            if "Event loop stopped before Future completed" not in str(exc) and "Event loop is closed" not in str(exc):
                logger.exception("BLE scanner loop runtime error")
                self._state_manager.set_bluetooth_unavailable()
        except Exception:
            logger.exception("BLE scanner loop crashed")
            self._state_manager.set_bluetooth_unavailable()
        finally:
            self._loop.close()
            self._running = False

    async def _scan(self) -> None:
        """Continuous BLE scanning with automatic restart on failure."""
        while self._running:
            try:
                self._scanner = BleakScanner(
                    detection_callback=self._on_advertisement,
                )
                self._state_manager.set_bluetooth_available()
                logger.info("Starting BLE scan…")
                await self._scanner.start()

                # Keep scanning until stopped
                while self._running:
                    await asyncio.sleep(1.0)

                await self._scanner.stop()

            except OSError as exc:
                logger.warning("BLE adapter error: %s — retrying in 10s", exc)
                self._state_manager.set_bluetooth_unavailable()
                # Wait 10 seconds before retrying (unless we're shutting down)
                for _ in range(100):
                    if not self._running:
                        return
                    await asyncio.sleep(0.1)

            except Exception as exc:
                logger.exception("Unexpected BLE error: %s", exc)
                self._state_manager.set_bluetooth_unavailable()
                await asyncio.sleep(5.0)

    def _on_advertisement(
        self,
        device: BLEDevice,
        adv_data: AdvertisementData,
    ) -> None:
        """Callback invoked for every BLE advertisement received.

        Filters for Apple manufacturer data and pushes parsed AirPods
        battery info to the state manager.
        """
        if not self._running:
            return

        if not adv_data.manufacturer_data:
            return

        # Check for Apple's company ID
        apple_data = adv_data.manufacturer_data.get(APPLE_COMPANY_ID)
        if apple_data is None:
            return

        # Try to parse as AirPods proximity pairing data
        parsed = parse_manufacturer_data(APPLE_COMPANY_ID, bytes(apple_data))
        if parsed is not None:
            logger.debug(
                "AirPods detected: %s | L:%s R:%s C:%s",
                parsed.model,
                parsed.left_battery,
                parsed.right_battery,
                parsed.case_battery,
            )
            self._state_manager.update_from_airpods(parsed)
