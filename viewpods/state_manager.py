"""
Thread-safe state manager for AirPods device connection and battery status.

Implements the observer pattern so the UI can react to state changes
without polling. Handles timeout-based disconnect detection.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

from viewpods.packet_parser import AirPodsData

logger = logging.getLogger(__name__)

# Seconds of BLE silence before device is considered disconnected
DISCONNECT_TIMEOUT = 30.0

# Minimum seconds between state-change callbacks (prevent UI thrashing)
DEBOUNCE_INTERVAL = 0.5


class ConnectionState(Enum):
    """Device connection state."""
    DISCONNECTED = auto()
    CONNECTED = auto()


@dataclass
class DeviceState:
    """Complete snapshot of the current device status."""

    connection: ConnectionState = ConnectionState.DISCONNECTED
    airpods: Optional[AirPodsData] = None
    last_seen: float = 0.0                  # time.monotonic() timestamp
    bluetooth_available: bool = True        # False if adapter is missing/off
    classic_device_name: Optional[str] = None  # Name from Windows BT Classic

    @property
    def is_connected(self) -> bool:
        return self.connection == ConnectionState.CONNECTED

    @property
    def is_low_battery(self) -> bool:
        """True if any available battery level is ≤ 20%."""
        if self.airpods is None:
            return False
        for level in (
            self.airpods.left_battery,
            self.airpods.right_battery,
            self.airpods.case_battery,
        ):
            if level is not None and level <= 20:
                return True
        return False


# Type alias for observer callbacks
StateCallback = Callable[[DeviceState], None]


class StateManager:
    """Thread-safe, observable device state container.

    Usage:
        sm = StateManager()
        sm.add_observer(my_callback)       # UI subscribes
        sm.update_from_airpods(data)       # BLE scanner pushes updates
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = DeviceState()
        self._observers: list[StateCallback] = []
        self._history: deque[AirPodsData] = deque(maxlen=5)
        self._last_notify_time: float = 0.0
        self._timeout_thread: Optional[threading.Thread] = None
        self._running = False

    # ── Observers ────────────────────────────────────────────────────────

    def add_observer(self, callback: StateCallback) -> None:
        """Register a function to be called on state changes."""
        with self._lock:
            self._observers.append(callback)

    def remove_observer(self, callback: StateCallback) -> None:
        with self._lock:
            self._observers.remove(callback)

    # ── State Access ─────────────────────────────────────────────────────

    @property
    def state(self) -> DeviceState:
        """Return a snapshot of the current state. Thread-safe read."""
        with self._lock:
            return self._state

    # ── State Mutations ──────────────────────────────────────────────────

    def update_from_airpods(self, data: AirPodsData) -> None:
        """Handle new AirPods data from the BLE scanner."""
        with self._lock:
            self._history.append(data)
            smoothed_data = self._compute_smoothed_data(data)

            self._state = DeviceState(
                connection=ConnectionState.CONNECTED,
                airpods=smoothed_data,
                last_seen=time.monotonic(),
                bluetooth_available=True,
                classic_device_name=self._state.classic_device_name,
            )
        self._notify_observers()

    def _compute_smoothed_data(self, latest: AirPodsData) -> AirPodsData:
        """Apply a mode filter to recent battery readings to stabilize UI."""
        if not self._history:
            return latest

        def get_mode(attr: str):
            values = [getattr(d, attr) for d in self._history if getattr(d, attr) is not None]
            if not values:
                return getattr(latest, attr)
            counts = Counter(values)
            return counts.most_common(1)[0][0]

        return AirPodsData(
            left_battery=get_mode("left_battery"),
            right_battery=get_mode("right_battery"),
            case_battery=get_mode("case_battery"),
            left_charging=get_mode("left_charging"),
            right_charging=get_mode("right_charging"),
            case_charging=get_mode("case_charging"),
            model=latest.model,
            raw_status=latest.raw_status,
        )

    def mark_disconnected(self) -> None:
        """Explicitly mark device as disconnected (timeout or manual)."""
        with self._lock:
            if (
                self._state.connection == ConnectionState.DISCONNECTED
                and self._state.classic_device_name is None
            ):
                return  # Already disconnected, skip duplicate notification
            self._state = DeviceState(
                connection=ConnectionState.DISCONNECTED,
                airpods=None,
                last_seen=self._state.last_seen,
                bluetooth_available=self._state.bluetooth_available,
                classic_device_name=None,
            )
        self._notify_observers()

    def mark_connected_classic(self, device_name: str) -> None:
        """Mark AirPods as connected via Bluetooth Classic (no battery data)."""
        with self._lock:
            # Don't overwrite BLE data if we already have it
            if self._state.connection == ConnectionState.CONNECTED and self._state.airpods:
                return
            self._state = DeviceState(
                connection=ConnectionState.CONNECTED,
                airpods=self._state.airpods,
                last_seen=time.monotonic(),
                bluetooth_available=True,
                classic_device_name=device_name,
            )
        self._notify_observers()

    def mark_classic_disconnected(self) -> None:
        """Mark Bluetooth Classic connection as lost."""
        with self._lock:
            # Only disconnect if we don't also have fresh BLE data
            if self._state.airpods and self._state.last_seen > 0:
                elapsed = time.monotonic() - self._state.last_seen
                if elapsed < DISCONNECT_TIMEOUT:
                    # BLE data is still fresh, just clear classic name
                    self._state = DeviceState(
                        connection=self._state.connection,
                        airpods=self._state.airpods,
                        last_seen=self._state.last_seen,
                        bluetooth_available=self._state.bluetooth_available,
                        classic_device_name=None,
                    )
                    return
            self._state = DeviceState(
                connection=ConnectionState.DISCONNECTED,
                airpods=None,
                last_seen=self._state.last_seen,
                bluetooth_available=self._state.bluetooth_available,
                classic_device_name=None,
            )
        self._notify_observers()

    def set_bluetooth_unavailable(self) -> None:
        """Mark Bluetooth adapter as unavailable."""
        with self._lock:
            self._state = DeviceState(
                connection=ConnectionState.DISCONNECTED,
                airpods=None,
                last_seen=0.0,
                bluetooth_available=False,
            )
        self._notify_observers()

    def set_bluetooth_available(self) -> None:
        """Mark Bluetooth adapter as available again."""
        with self._lock:
            self._state = DeviceState(
                connection=self._state.connection,
                airpods=self._state.airpods,
                last_seen=self._state.last_seen,
                bluetooth_available=True,
            )
        # Don't notify — the scanner resuming will trigger updates

    # ── Timeout Checker ──────────────────────────────────────────────────

    def start_timeout_checker(self) -> None:
        """Start a background thread that checks for BLE silence timeouts."""
        self._running = True
        self._timeout_thread = threading.Thread(
            target=self._timeout_loop,
            name="StateTimeout",
            daemon=True,
        )
        self._timeout_thread.start()

    def stop(self) -> None:
        """Stop the timeout checker."""
        self._running = False
        if self._timeout_thread and self._timeout_thread.is_alive():
            self._timeout_thread.join(timeout=2.0)

    def _timeout_loop(self) -> None:
        """Periodically check if BLE packets have stopped arriving."""
        while self._running:
            time.sleep(5.0)  # Check every 5 seconds — lightweight
            with self._lock:
                if (
                    self._state.connection == ConnectionState.CONNECTED
                    and self._state.last_seen > 0
                    and (time.monotonic() - self._state.last_seen) > DISCONNECT_TIMEOUT
                ):
                    needs_disconnect = True
                else:
                    needs_disconnect = False

            if needs_disconnect:
                logger.info("AirPods timeout — marking disconnected")
                self.mark_disconnected()

    # ── Internal ─────────────────────────────────────────────────────────

    def _notify_observers(self) -> None:
        """Invoke all observer callbacks with the current state."""
        now = time.monotonic()
        with self._lock:
            # Debounce rapid-fire updates
            if (now - self._last_notify_time) < DEBOUNCE_INTERVAL:
                return
            self._last_notify_time = now
            observers = list(self._observers)
            state_snapshot = self._state

        for cb in observers:
            try:
                cb(state_snapshot)
            except Exception:
                logger.exception("Observer callback error")
