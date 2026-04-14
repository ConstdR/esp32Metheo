# ESP32 Weather Station

Weather station based on ESP32 with BME280 sensor. Measures temperature,
humidity, pressure and battery voltage. Data is sent over local network
via **MQTT-UDP** (UDP broadcast, port 1883) and stored in SQLite by the server.

Board is powered by a Li-Ion 18650 battery.

---

## Repository Structure

```
esp32Metheo/
├── esp32/      ← MicroPython firmware
├── espidf/     ← ESP-IDF firmware (C)
└── server/     ← Python server (listenudp.py, web.py, SQLite)
```

---

## Hardware

```
BME280        ESP32
─────────────────────
VCC    →    3.3V
GND    →    GND
SDA    →    GPIO 21
SCL    →    GPIO 22
SDO    →    GND       ← address 0x76
CSB    →    3.3V      ← force I2C mode

Battery voltage divider:
BAT(+) → R1(110k) → GPIO 34 → R2(110k) → GND

Solar panel voltage divider:
SOLAR(+) → R1(110k) → GPIO 33 → R2(110k) → GND

LED indicator:
GPIO 5 → 220Ω resistor → LED(+) → LED(-) → GND
```

---

## Firmware Variants

### ESP-IDF (C) — `espidf/`

Full-featured firmware using ESP-IDF framework:

* Deep sleep between measurements (15 min default)
* NTP time sync once per 24 hours (stored in RTC memory)
* **Wi-Fi configuration via captive portal** (AP mode on first boot)
* Device ID from MAC address
* Automatic AP mode after repeated connection failures

#### First boot / Wi-Fi setup

On first boot (or when no credentials are stored), the device starts
an open Wi-Fi access point named **Metheo_XXXXXX** (last 6 chars of MAC).
Connect to it and open `http://192.168.4.1` to enter Wi-Fi credentials.
LED blinks rapidly in AP mode. After 5 minutes with no config, the device
goes to deep sleep and retries on next wake.

#### Erasing stored credentials

To reset Wi-Fi credentials and force AP mode:

```bash
idf.py erase-flash
idf.py flash
```

See [`espidf/`](espidf/) for build instructions.

### MicroPython — `esp32/`

Original MicroPython firmware:
- AP configuration mode on first boot
- Solar panel voltage monitoring on GPIO 33
- All pins configurable via `espconf.py`

---

## Data Format

Both firmware variants send MQTT-UDP packets to topic `weather/<device_id>`:

```json
{"ts":"2026-04-13T10:00:00","t":21.9,"h":24.7,"p":976.6,"v":3.85}
```

| Field | Description       | Unit     |
|-------|-------------------|----------|
| `ts`  | UTC timestamp     | ISO 8601 |
| `t`   | Temperature       | °C       |
| `h`   | Humidity          | %        |
| `p`   | Pressure          | hPa      |
| `v`   | Supply voltage    | V        |
| `vs`  | Solar voltage     | V        |

---

## Server

```bash
cd server
./start.sh
```

- **`listenudp.py`** — receives MQTT-UDP packets and stores to SQLite
- **`web.py`** — web interface with current values and history graphs
- Docker support via `Dockerfile` / `Makefile`

More info: [MQTT-UDP](https://mqtt-udp.readthedocs.io/en/latest/)
