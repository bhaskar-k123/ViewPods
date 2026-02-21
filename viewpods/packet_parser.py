"""
Apple Continuity Protocol — AirPods BLE advertisement packet parser.

Decodes manufacturer-specific data (company ID 0x004C) to extract
battery levels and charging status for AirPods Pro 2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Apple's Bluetooth SIG company identifier
APPLE_COMPANY_ID = 0x004C

# Proximity Pairing message type in Apple Continuity Protocol
PROXIMITY_PAIRING_TYPE = 0x07

# Known AirPods Pro 2 model IDs (Lightning and USB-C variants)
AIRPODS_PRO_2_MODELS: dict[int, str] = {
    0x1420: "AirPods Pro 2",
    0x1520: "AirPods Pro 2 (USB-C)",
}

# Extended set of recognized Apple earphone models for broader compatibility
KNOWN_APPLE_MODELS: dict[int, str] = {
    **AIRPODS_PRO_2_MODELS,
    0x2002: "AirPods",
    0x200F: "AirPods 2",
    0x200E: "AirPods Pro",
    0x2014: "AirPods 3",
    0x2024: "AirPods Pro 2",
    0x2013: "AirPods Max",
    0x6465: "AirPods Pro",
    0x4abf: "AirPods Pro",
}

# Battery nibble value indicating "not available"
BATTERY_UNAVAILABLE = 0xF


@dataclass(frozen=True)
class AirPodsData:
    """Parsed battery information from an AirPods BLE advertisement."""

    left_battery: Optional[int] = None       # 0–100 (%) or None if unavailable
    right_battery: Optional[int] = None      # 0–100 (%) or None if unavailable
    case_battery: Optional[int] = None       # 0–100 (%) or None if unavailable
    left_charging: bool = False
    right_charging: bool = False
    case_charging: bool = False
    model: str = "Unknown AirPods"
    raw_status: int = 0                      # Raw status byte for diagnostics


def _nibble_to_percent(nibble: int) -> Optional[int]:
    """Convert a 4-bit battery nibble (0–10) to a percentage (0–100).

    Returns None if the nibble value indicates unavailability (0xF).
    """
    if nibble == BATTERY_UNAVAILABLE or nibble > 10:
        return None
    return nibble * 10


def parse_manufacturer_data(
    company_id: int,
    data: bytes,
) -> Optional[AirPodsData]:
    """Parse Apple manufacturer-specific BLE advertisement data.

    Args:
        company_id: The 16-bit BLE company identifier.
        data: Raw manufacturer data payload (after the company ID).

    Returns:
        An AirPodsData instance if the packet is a valid AirPods Pro 2
        proximity pairing message, or None if the packet should be ignored.
    """
    if company_id != APPLE_COMPANY_ID:
        return None

    # Need at least 15 bytes for a basic proximity pairing message
    if len(data) < 15:
        return None

    # The Continuity message may start a few bytes into the payload.
    # We search for the proximity pairing type byte (0x07) followed by
    # a length byte of 0x19 (25), which is the standard payload length.
    offset = _find_proximity_pairing_offset(data)
    if offset is None:
        return None

    try:
        return _decode_proximity_pairing(data, offset)
    except (IndexError, ValueError) as exc:
        logger.debug("Failed to decode proximity pairing data: %s", exc)
        return None


def _find_proximity_pairing_offset(data: bytes) -> Optional[int]:
    """Locate the start of a Proximity Pairing message within the data.

    Apple Continuity Protocol messages are TLV-encoded. We scan for
    type=0x07 with length=0x19 which indicates a Proximity Pairing message.
    """
    i = 0
    while i < len(data) - 1:
        msg_type = data[i]
        msg_len = data[i + 1]

        if msg_type == PROXIMITY_PAIRING_TYPE and msg_len in (0x19, 0x11):
            # Verify we have enough data from this offset
            if i + 2 + msg_len <= len(data):
                return i
        # Move to the next TLV entry
        i += 2 + msg_len if msg_len > 0 else i + 2
    return None


def _decode_proximity_pairing(data: bytes, offset: int) -> Optional[AirPodsData]:
    """Decode battery and status info from a Proximity Pairing message.

    Byte layout (relative to the type byte at `offset`):
        [0]  = Message type (0x07)
        [1]  = Length (0x19 = 25)
        [2]  = Prefix / reserved
        [3]  = Device model (high byte)
        [4]  = Device model (low byte)
        [5]  = Status byte
        [6]  = Pods battery byte (left nibble + right nibble)
        [7]  = Flags + Case battery (charging flags nibble + case nibble)
        [8]  = Lid open counter
        [9+] = Encrypted / reserved data
    """
    base = offset + 2  # skip type + length bytes

    # Extract device model
    model_id = (data[base + 1] << 8) | data[base + 2]
    model_name = KNOWN_APPLE_MODELS.get(model_id)

    if model_name is None:
        # Fallback for unrecognized models that are clearly AirPods proximity packets
        model_name = "AirPods"

    status_byte = data[base + 3]
    pods_byte = data[base + 4]
    flags_case_byte = data[base + 5]

    # The status byte bit 5 indicates if left/right are flipped
    flipped = (status_byte & 0x02) != 0

    # Extract battery nibbles
    left_nibble = (pods_byte >> 4) & 0x0F
    right_nibble = pods_byte & 0x0F

    if flipped:
        left_nibble, right_nibble = right_nibble, left_nibble

    # Case battery is the lower nibble of the flags/case byte
    case_nibble = flags_case_byte & 0x0F

    # Charging flags are the upper nibble of the flags/case byte
    charge_flags = (flags_case_byte >> 4) & 0x0F
    left_charging = bool(charge_flags & 0x02) if not flipped else bool(charge_flags & 0x01)
    right_charging = bool(charge_flags & 0x01) if not flipped else bool(charge_flags & 0x02)
    case_charging = bool(charge_flags & 0x04)

    return AirPodsData(
        left_battery=_nibble_to_percent(left_nibble),
        right_battery=_nibble_to_percent(right_nibble),
        case_battery=_nibble_to_percent(case_nibble),
        left_charging=left_charging,
        right_charging=right_charging,
        case_charging=case_charging,
        model=model_name,
        raw_status=status_byte,
    )
