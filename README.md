# ESP32 Weather Station

Weather station based on ESP32 with BME280 or SHT30 sensor. Measures temperature,
humidity, pressure (BME280 only) and battery voltage. Data is sent over local network
via **MQTT-UDP** (UDP broadcast, port 1883) and stored in SQLite by the server.

Board is powered by a Li-Ion 18650 battery with optional solar panel charging.

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
BME280/SHT30  ESP32
─────────────────────
VCC    →    3.3V
GND    →    GND
SDA    →    GPIO 21
SCL    →    GPIO 22

BME280 only:
  SDO  →    GND       ← address 0x76
  CSB  →    3.3V      ← force I2C mode

SHT30:
  address 0x44 (default)

Battery voltage divider:
BAT(+) → R1(110k) → GPIO 34 → R2(110k) → GND

Solar panel voltage divider (optional):
SOLAR(+) → R1(110k) → GPIO 33 → R2(110k) → GND

LED indicator:
GPIO 5 → 220Ω resistor → LED(+) → LED(-) → GND
```

---

## Firmware Variants

### ESP-IDF (C) — `espidf/`

Full-featured firmware using ESP-IDF framework:

* **Sensor auto-detection** — probes SHT30 (0x44) first, falls back to BME280 (0x76)
* Deep sleep between measurements (configurable, 15 min default)
* Timestamps assigned by server (no NTP dependency on device)
* **Wi-Fi configuration via captive portal** (AP mode on first boot)
* Device ID from MAC address
* Automatic AP mode after repeated connection failures
* Optional solar panel voltage monitoring
* All GPIO pins and timings configurable via `idf.py menuconfig`

#### Building

```bash
cd espidf
idf.py build
idf.py flash monitor
```

#### Configuration (`idf.py menuconfig`)

Under **Weather Station Configuration**:

| Option | Default | Description |
|--------|---------|-------------|
| Deep sleep interval | 15 min | Time between measurements (1–60 min) |
| LED GPIO pin | 5 | Status LED pin |
| AP mode timeout | 300 s | Captive portal timeout before deep sleep |
| Wi-Fi failures before AP | 5 | Consecutive failures before AP mode |
| Battery ADC GPIO | 34 | Battery voltage divider pin (GPIO 32–39) |
| Sensor type | Auto-detect | Auto (SHT30→BME280), BME280 only, SHT30 only |
| Solar panel enabled | yes | Enable solar voltage measurement |
| Solar ADC GPIO | 33 | Solar panel voltage divider pin (GPIO 32–39) |

#### First boot / Wi-Fi setup

On first boot (or when no credentials are stored), the device starts
an open Wi-Fi access point named **Metheo_XXXXXX** (last 6 chars of MAC).
Connect to it and open `http://192.168.4.1` to enter Wi-Fi credentials.
LED blinks rapidly in AP mode. After 5 minutes with no config, the device
goes to deep sleep and retries on next wake.

If connection fails, the AP page will show the SSID and password that
were used, so you can spot typos.

#### Erasing stored credentials

To reset Wi-Fi credentials and force AP mode:

```bash
idf.py erase-flash
idf.py flash
```

### MicroPython — `esp32/`

Original MicroPython firmware:

* AP configuration mode on first boot
* SHT30 / BME280 auto-detection
* Solar panel voltage monitoring on GPIO 33
* All pins configurable via `espconf.py`

---

## Data Format

Both firmware variants send MQTT-UDP packets to topic `weather/<device_id>`:

```json
{"t":21.9,"h":24.7,"p":976.6,"v":3.85,"vs":4.12}
```

Timestamp is assigned by the server upon receipt (UTC).

| Field | Description | Unit | Notes |
|-------|-------------|------|-------|
| `t` | Temperature | °C | |
| `h` | Humidity | % | |
| `p` | Pressure | hPa | 0 when using SHT30 |
| `v` | Supply voltage | V | |
| `vs` | Solar voltage | V | omitted if solar disabled |
| `lbat` | Low battery flag | — | only present when voltage below threshold |

---

## Server

```bash
cd server
./start.sh
```

* **`listenudp.py`** — receives MQTT-UDP packets and stores to SQLite
* **`web.py`** — web interface with current values and history graphs
* Docker support via `Dockerfile` / `Makefile`

More info: [MQTT-UDP](https://mqtt-udp.readthedocs.io/en/latest/)
