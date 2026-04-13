# Weather Station — ESP32 + BME280 + MQTT-UDP

Проект метеостанции на ESP32 с сенсором BME280. Данные отправляются
в локальную сеть через **MQTT-UDP** (UDP broadcast, порт 1883) — без
брокера, без TCP.

---

## Железо

```
BME280        ESP32
─────────────────────
VCC    →    3.3V
GND    →    GND
SDA    →    GPIO 21
SCL    →    GPIO 22
SDO    →    GND       ← адрес 0x76
CSB    →    3.3V      ← принудительно I2C режим
```

> Если SDO подключён к 3.3V — адрес 0x77.
> Измени `BME280_ADDR` в `sensor.c` на `BME280_I2C_ADDR_SEC`.

---

## Быстрый старт

### 1. Настройка ESP-IDF (Gentoo / Linux)

```bash
# Активация окружения (нужно каждый раз в новом терминале)
. ~/esp/esp-idf/export.sh
```

### 2. Настройка Wi-Fi

Открой `main/main.c` и измени:

```c
#define WIFI_SSID      "your_ssid"
#define WIFI_PASSWORD  "your_password"
```

### 3. Добавить зависимость BME280

```bash
cd weather_station
idf.py add-dependency "espressif/bme280^0.0.1"
```

### 4. Сборка и прошивка

```bash
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

> **Gentoo**: если порт недоступен без sudo:
> ```bash
> sudo gpasswd -a $USER uucp
> newgrp uucp
> ```

---

## Структура проекта

```
weather_station/
├── CMakeLists.txt
├── listen_mqttudp.py       ← слушатель на сервере (Python)
├── README.md
└── main/
    ├── CMakeLists.txt
    ├── idf_component.yml   ← зависимость espressif/bme280
    ├── main.c              ← точка входа, задача FreeRTOS
    ├── wifi.c / wifi.h     ← Wi-Fi Station с авто-переподключением
    ├── sensor.c / sensor.h ← I2C + BME280 драйвер
    └── mqttudp_client.c / mqttudp_client.h  ← UDP PUBLISH
```

---

## MQTT топики

| Топик                  | Значение          | Пример  |
|------------------------|-------------------|---------|
| `weather/temperature`  | Температура, °C   | `23.4`  |
| `weather/humidity`     | Влажность, %      | `48.2`  |
| `weather/pressure`     | Давление, hPa     | `1013.2`|

Интервал отправки: **30 секунд** (меняется через `SENSOR_INTERVAL_MS` в `main.c`).

---

## Приём данных на сервере

```bash
python3 listen_mqttudp.py
```

Вывод:
```
Listening on UDP :1883 ...
Time          From              Topic                         Value
──────────────────────────────────────────────────────────────────
14:05:32      192.168.1.42      weather/temperature           23.4
14:05:32      192.168.1.42      weather/humidity              48.2
14:05:32      192.168.1.42      weather/pressure              1013.2
```

---

## Возможные проблемы

| Проблема | Решение |
|---|---|
| `BME280 init failed` | Проверь провода SDA/SCL, питание 3.3V |
| Нет данных в сети | Проверь что сервер в той же подсети |
| `/dev/ttyUSB0` не найден | `ls /dev/ttyUSB* /dev/ttyACM*` |
| Ошибка прав на порт | `sudo gpasswd -a $USER uucp && newgrp uucp` |
| CH340 не определяется | `sudo modprobe ch341` |
