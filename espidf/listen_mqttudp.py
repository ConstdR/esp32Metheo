#!/usr/bin/env python3
"""
listen_mqttudp.py — слушатель MQTT-UDP от ESP32 weather station.

Запуск:
    python3 listen_mqttudp.py
"""

import socket
import json
import datetime

PORT = 1885


def parse_mqtt_publish(data: bytes):
    if len(data) < 4 or (data[0] & 0xF0) != 0x30:
        return None
    topic_len = (data[2] << 8) | data[3]
    if len(data) < 4 + topic_len:
        return None
    topic   = data[4 : 4 + topic_len].decode("utf-8", errors="replace")
    payload = data[4 + topic_len :].decode("utf-8", errors="replace")
    return topic, payload


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind(("", PORT))

    print(f"Listening on UDP :{PORT} ...")
    print(f"{'Time':<12}  {'From':<16}  {'Topic':<32}  Payload")
    print("-" * 80)

    # Дедупликация по (topic, ts)
    last_seen = {}  # topic → последний ts

    while True:
        raw, addr = sock.recvfrom(512)
        result = parse_mqtt_publish(raw)
        if result is None:
            continue
        topic, payload = result

        try:
            j = json.loads(payload)
            ts_val = j.get("ts")

            # Дедупликация по (topic, ts)
            if ts_val is not None:
                if last_seen.get(topic) == ts_val:
                    continue
                last_seen[topic] = ts_val

            pretty = (f"T={j['t']}°C  "
                      f"H={j['h']}%  "
                      f"P={j['p']}hPa  "
                      f"ts={ts_val}")
        except Exception:
            pretty = payload

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"{ts:<12}  {addr[0]:<16}  {topic:<32}  {pretty}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
