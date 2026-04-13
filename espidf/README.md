# Weather Station — ESP32 + BME280 + MQTT-UDP (ESP-IDF)

Метеостанция на ESP32 с сенсором BME280. Данные отправляются в локальную
сеть через **MQTT-UDP** (UDP broadcast, порт 1883) и принимаются сервером
из каталога `../server/`.

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

Делитель напряжения питания:
BAT(+) → R1(110k) → GPIO 34 → R2(110k) → GND

LED индикатор:
GPIO 5 → резистор 220Ω → LED(+) → LED(-) → GND
```

> Если SDO подключён к 3.3V — адрес 0x77.
> Измени `BME280_ADDR` в `sensor.c` на `BME280_I2C_ADDRESS_DEFAULT + 1`.

---

## Быстрый старт

### 1. Активация ESP-IDF

```bash
. ~/esp/esp-idf/export.sh
```

### 2. Настройка параметров

```bash
cd espidf
idf.py menuconfig
```

В разделе **Weather Station Configuration** задай:
- Wi-Fi SSID и пароль
- Интервал deep sleep (по умолчанию 15 минут)
- GPIO для LED (по умолчанию 5)

### 3. Сборка и прошивка

```bash
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

> **Gentoo**: если порт недоступен без sudo:
> ```bash
> sudo gpasswd -a $USER uucp && newgrp uucp
> ```

---

## Структура проекта

```
espidf/
├── CMakeLists.txt
├── sdkconfig.defaults       ← шаблон настроек
├── listen_mqttudp.py        ← простой отладочный слушатель
└── main/
    ├── CMakeLists.txt
    ├── Kconfig.projbuild    ← параметры menuconfig
    ├── idf_component.yml    ← зависимости (bme280, i2c_bus)
    ├── main.c               ← точка входа, NTP, deep sleep
    ├── wifi.c / wifi.h      ← Wi-Fi Station
    ├── sensor.c / sensor.h  ← BME280 + ADC напряжения
    └── mqttudp_client.c/h   ← MQTT-UDP отправка
```

---

## Поведение устройства

```
boot/wake → Wi-Fi → NTP (раз в 24ч) → BME280 → отправка → deep sleep
```

- **Deep sleep**: 15 минут (настраивается через menuconfig)
- **NTP**: синхронизация раз в сутки, время хранится в RTC memory
- **LED**: горит во время измерения и отправки данных
- **Device ID**: MAC адрес Wi-Fi интерфейса

---

## Формат данных MQTT-UDP

- **Топик**: `weather/<device_id>` (например `weather/ac67b2386628`)
- **Payload**:

```json
{"ts":"2026-04-13T10:00:00","t":21.9,"h":24.7,"p":976.6,"v":3.85}
```

| Поле | Описание         | Единица |
|------|------------------|---------|
| `ts` | Время UTC        | ISO 8601 |
| `t`  | Температура      | °C      |
| `h`  | Влажность        | %       |
| `p`  | Давление         | hPa     |
| `v`  | Напряжение питания | В     |

---

## Приём данных

Для отладки — простой слушатель:
```bash
python3 listen_mqttudp.py
```

Для сохранения в БД — используй `../server/listenudp.py`.

---

## Возможные проблемы

| Проблема | Решение |
|---|---|
| `BME280 init failed` | Проверь провода SDA=21, SCL=22, питание 3.3V |
| `Brownout detector` | Используй качественный кабель или блок питания 1A+ |
| Нет данных в сети | Проверь что сервер в той же подсети, порт 1883 |
| `/dev/ttyUSB0` не найден | `ls /dev/ttyUSB* /dev/ttyACM*` |
| Ошибка прав на порт | `sudo gpasswd -a $USER uucp && newgrp uucp` |
| CH340 не определяется | `sudo modprobe ch341` |
