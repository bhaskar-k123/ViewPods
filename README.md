# ViewPods

**AirPods Pro 2 Battery Monitor for Windows 11**

A lightweight utility that monitors AirPods Pro 2 battery status via Bluetooth Low Energy and displays real-time information through a beautiful dark-mode window.

![Dark Mode UI](https://img.shields.io/badge/UI-Dark_Mode-1e1e2e?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-11-0078D4?style=for-the-badge&logo=windows11&logoColor=white)

---

## Features

- **Real-time BLE monitoring** — Event-driven, zero-polling detection of AirPods Pro 2
- **Beautiful dark UI** — Catppuccin Mocha-inspired window with animated battery bars
- **Auto-detection** — Battery data appears automatically when AirPods are nearby
- **Auto-disconnect** — UI reverts when AirPods move out of range (30s timeout)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python -m viewpods
```

## Architecture

```
viewpods/
├── main.py            # Entry point & orchestration
├── ble_scanner.py     # Async BLE scanner (bleak, daemon thread)
├── packet_parser.py   # Apple Continuity Protocol decoder
├── state_manager.py   # Thread-safe state with observer pattern
└── ui_window.py       # Status window (customtkinter, dark theme)
```

## Battery Display

| Component | Value | Indicator |
|-----------|-------|-----------|
| Left earbud | 0–100% | Green / Yellow / Red bar |
| Right earbud | 0–100% | Green / Yellow / Red bar |
| Charging case | 0–100% | Green / Yellow / Red bar |
| Charging | Per-component | ⚡ Charging label |

## How It Works

1. `bleak.BleakScanner` listens for BLE advertisements in a daemon thread
2. Advertisements with Apple's company ID (`0x004C`) are filtered
3. The Proximity Pairing message (type `0x07`) is decoded for battery nibbles
4. State changes propagate to the UI via the observer pattern
5. The window updates battery bars and status in real time

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## License

MIT
