"""
Windows Bluetooth classic device checker — detects connected AirPods.

Uses Windows PnP device enumeration to detect when AirPods are connected
via Bluetooth Classic (A2DP audio profile), which is the primary connection
mode when AirPods are actively in use. This supplements the BLE scanner
which can only detect battery data from proximity pairing advertisements.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# How often to poll for connected devices (seconds)
POLL_INTERVAL = 5.0

# PowerShell command to find paired Bluetooth AirPods devices
_PS_COMMAND = (
    "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue"
    " | Where-Object { $_.FriendlyName -like '*AirPod*'"
    "   -and $_.FriendlyName -notlike '*Avrcp*'"
    "   -and $_.FriendlyName -notlike '*Find My*' }"
    " | Select-Object Status, FriendlyName"
    " | ConvertTo-Json -Compress"
)


def _check_airpods_connected() -> Optional[str]:
    """Check if any AirPods device is connected via Windows Bluetooth.

    Returns:
        The device name (e.g. 'AirPods Pro') if connected, else None.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_COMMAND],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        import json
        data = json.loads(result.stdout.strip())

        # PowerShell returns a single object (dict) or a list
        if isinstance(data, dict):
            data = [data]

        for device in data:
            status = device.get("Status", "")
            name = device.get("FriendlyName", "")
            if status == "OK" and name:
                logger.debug("AirPods connected (classic BT): %s", name)
                return name

    except subprocess.TimeoutExpired:
        logger.warning("Bluetooth device check timed out")
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.debug("Failed to parse BT device info: %s", exc)
    except FileNotFoundError:
        logger.warning("PowerShell not found — cannot check classic BT")
    except Exception:
        logger.exception("Unexpected error checking Bluetooth devices")

    return None


class BtDeviceChecker:
    """Polls Windows for connected AirPods via Bluetooth Classic.

    Runs in a background daemon thread. When an AirPods device is
    detected as connected (Status=OK), it notifies the state manager.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._on_connected: Optional[callable] = None
        self._on_disconnected: Optional[callable] = None
        self._was_connected = False

    def start(
        self,
        on_connected: callable,
        on_disconnected: callable,
    ) -> None:
        """Start polling in a background thread.

        Args:
            on_connected: Called with device name when AirPods are detected.
            on_disconnected: Called when AirPods are no longer detected.
        """
        if self._running:
            return
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="BtDeviceChecker",
            daemon=True,
        )
        self._thread.start()
        logger.info("Bluetooth device checker started")

    def stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("Bluetooth device checker stopped")

    def _poll_loop(self) -> None:
        """Periodically check for connected AirPods."""
        while self._running:
            try:
                name = _check_airpods_connected()

                if name and not self._was_connected:
                    self._was_connected = True
                    if self._on_connected:
                        self._on_connected(name)

                elif not name and self._was_connected:
                    self._was_connected = False
                    if self._on_disconnected:
                        self._on_disconnected()

            except Exception:
                logger.exception("Error in BT device checker loop")

            # Sleep in small increments so we can stop quickly
            for _ in range(int(POLL_INTERVAL * 10)):
                if not self._running:
                    return
                time.sleep(0.1)
