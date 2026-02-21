"""
Unit tests for the Apple Continuity Protocol packet parser.
"""

import pytest

from viewpods.packet_parser import (
    AIRPODS_PRO_2_MODELS,
    APPLE_COMPANY_ID,
    AirPodsData,
    parse_manufacturer_data,
)


def _build_proximity_pairing_payload(
    model_hi: int = 0x14,
    model_lo: int = 0x20,
    status: int = 0x00,
    pods_byte: int = 0xA5,   # Left=10 (100%), Right=5 (50%)
    flags_case: int = 0x07,  # Charging flags=0, Case=7 (70%)
    lid_counter: int = 0x01,
    pad_to: int = 27,
) -> bytes:
    """Build a synthetic AirPods Proximity Pairing BLE payload.

    Default values produce a valid AirPods Pro 2 packet with:
    - Left battery: 100%
    - Right battery: 50%
    - Case battery: 70%
    - No charging
    """
    # Message type 0x07, length 0x19 (25 bytes of content)
    header = bytes([0x07, 0x19])
    # Prefix + model + status + pods + flags/case + lid
    content = bytes([
        0x01,           # prefix/reserved
        model_hi,
        model_lo,
        status,
        pods_byte,
        flags_case,
        lid_counter,
    ])
    # Pad remaining content to fill length=0x19
    remaining = 0x19 - len(content)
    padding = bytes([0x00] * remaining)
    payload = header + content + padding

    # Pad overall payload to minimum length
    if len(payload) < pad_to:
        payload += bytes([0x00] * (pad_to - len(payload)))

    return payload


class TestParseManufacturerData:
    """Tests for parse_manufacturer_data()."""

    def test_valid_airpods_pro_2_packet(self):
        """Valid AirPods Pro 2 packet should return correct battery levels."""
        data = _build_proximity_pairing_payload(
            model_hi=0x14, model_lo=0x20,
            pods_byte=0xA5,   # Left=10 (100%), Right=5 (50%)
            flags_case=0x07,  # Case=7 (70%)
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.model == "AirPods Pro 2"
        assert result.left_battery == 100
        assert result.right_battery == 50
        assert result.case_battery == 70

    def test_usb_c_variant(self):
        """AirPods Pro 2 USB-C variant should also be recognized."""
        data = _build_proximity_pairing_payload(model_hi=0x15, model_lo=0x20)
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.model == "AirPods Pro 2 (USB-C)"

    def test_unavailable_battery(self):
        """Nibble value 0xF should produce None for battery level."""
        data = _build_proximity_pairing_payload(
            pods_byte=0xFF,   # Both pods unavailable
            flags_case=0x0F,  # Case unavailable
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.left_battery is None
        assert result.right_battery is None
        assert result.case_battery is None

    def test_non_apple_company_id(self):
        """Non-Apple company ID should be rejected."""
        data = _build_proximity_pairing_payload()
        result = parse_manufacturer_data(0x1234, data)
        assert result is None

    def test_unrecognized_model(self):
        """Unknown model ID should be rejected."""
        data = _build_proximity_pairing_payload(model_hi=0xFF, model_lo=0xFF)
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)
        assert result is None

    def test_truncated_data(self):
        """Too-short data should return None without crashing."""
        result = parse_manufacturer_data(APPLE_COMPANY_ID, bytes([0x07, 0x19, 0x01]))
        assert result is None

    def test_empty_data(self):
        """Empty data should return None."""
        result = parse_manufacturer_data(APPLE_COMPANY_ID, b"")
        assert result is None

    def test_wrong_message_type(self):
        """Non-proximity-pairing message type should be ignored."""
        data = bytearray(_build_proximity_pairing_payload())
        data[0] = 0x03  # Change type to something else
        result = parse_manufacturer_data(APPLE_COMPANY_ID, bytes(data))
        assert result is None

    def test_charging_flags(self):
        """Charging flags should be correctly decoded."""
        data = _build_proximity_pairing_payload(
            pods_byte=0xA5,
            flags_case=0x67,  # Charge flags=6 (left+case charging), Case=7
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.left_charging is True
        assert result.right_charging is False
        assert result.case_charging is True

    def test_flipped_pods(self):
        """When status bit indicates flipped, left/right should swap."""
        data = _build_proximity_pairing_payload(
            status=0x02,       # Flipped bit set
            pods_byte=0xA5,    # Raw: upper=A(10), lower=5
            flags_case=0x07,
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        # Flipped: left gets the lower nibble, right gets the upper
        assert result.left_battery == 50   # Was right (5)
        assert result.right_battery == 100  # Was left (A=10)

    def test_zero_battery(self):
        """Zero nibble should map to 0%."""
        data = _build_proximity_pairing_payload(
            pods_byte=0x00,
            flags_case=0x00,
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.left_battery == 0
        assert result.right_battery == 0
        assert result.case_battery == 0

    def test_full_battery(self):
        """Nibble 0xA (10) should map to 100%."""
        data = _build_proximity_pairing_payload(
            pods_byte=0xAA,   # Both at 10 = 100%
            flags_case=0x0A,  # Case at 10 = 100%
        )
        result = parse_manufacturer_data(APPLE_COMPANY_ID, data)

        assert result is not None
        assert result.left_battery == 100
        assert result.right_battery == 100
        assert result.case_battery == 100

    def test_17_byte_packet(self):
        """Test support for 17-byte (0x11) proximity pairing packets."""
        # 07 11 06 64 65 9c 45 a4 ...
        header = bytes([0x07, 0x11])
        content = bytes([
            0x06, # prefix
            0x64, 0x65, # model 0x6465
            0x9c, # status
            0x45, # pods (L:4, R:5)
            0xa4, # flags/case (Case:4)
        ])
        padding = bytes([0x00] * (0x11 - len(content)))
        payload = header + content + padding
        
        result = parse_manufacturer_data(APPLE_COMPANY_ID, payload)
        
        assert result is not None
        assert result.model == "AirPods Pro"
        assert result.left_battery == 40
        assert result.right_battery == 50
        assert result.case_battery == 40
