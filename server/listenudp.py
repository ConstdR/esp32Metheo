#!/usr/bin/env python3
"""
MQTT-UDP listener — receives sensor data and device config over UDP broadcast,
stores measurements in per-device SQLite databases.

Topics handled:
  weather/<device_id>         → sensor readings (t, h, p, v, vs) → table "data"
  weather/<device_id>/config  → device config (fw, sensor, gpio, etc.) → table "params"

Deduplication: identical consecutive packets from the same topic are ignored
(e.g. config packets that don't change between wake cycles).

Timestamp is assigned by the server (UTC), not by the device.
"""
import argparse, configparser, logging, re, sqlite3, json
from datetime import datetime, timezone
import mqttudp.engine as me

llg  = logging.getLogger(__name__)
last = {}   # {topic: last_value} — for deduplication
cfg  = None

# Fields to extract from sensor JSON → SQLite columns
# ts = server-assigned timestamp, ip = sender IP from UDP packet
DB_FIELDS = ['ts','ip','t','h','p','v','vs','m']

def main():
    global cfg
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', default='config.cfg', help='Config file. Default: config.cfg')
    config = configparser.ConfigParser()
    config.read(parser.parse_args().config)
    cfg = config['listener']
    llg.setLevel(cfg['debug'])
    me.listen(recv_packet)

def db_path(wid):   return f"{cfg['dbdir']}/{wid}.sqlite3"

def get_db(wid):
    dbh = sqlite3.connect(db_path(wid))
    return dbh, dbh.cursor()

def recv_packet(pkt):
    """Callback for each MQTT-UDP packet. Filters by type, deduplicates,
    and routes to store_conf() or store() based on topic pattern."""
    global last
    if pkt.ptype != me.PacketType['Publish']:           return
    # Skip duplicate packets (same topic + same payload as last time)
    if last.get(pkt.topic) == pkt.value:                return
    last[pkt.topic] = pkt.value
    llg.debug(f"{pkt.topic}={pkt.value}\t\t{pkt.addr}")
    for pattern, handler in (
        (r'^weather/+([^/]*)/config$', store_conf),
        (r'^weather/+([^/]*)$',        store),
    ):
        m = re.match(pattern, pkt.topic)
        if m and m.group(1):
            try:    handler(m.group(1), pkt.value) if handler is store_conf else handler(m.group(1), pkt.value, str(pkt.addr))
            except Exception as e: llg.error(f"{handler.__name__} error: {e}")

def store_conf(wid, data):
    """Save device config (JSON key-value pairs) to the 'params' table.
    Each key becomes a row: name=key, value=value. Used by web.py to
    determine firmware type, GPIO pins, sensor type, thresholds, etc."""
    dbh, c = get_db(wid)
    for k, v in json.loads(data).items():
        c.execute("INSERT OR REPLACE INTO params (name, value) VALUES (?,?)", (k, v))
    dbh.commit(); dbh.close()

def store(wid, data, ip):
    """Save sensor reading to the 'data' table. Timestamp is server UTC,
    not the device's ts field (device RTC drifts significantly).
    Creates tables on first insert for new devices."""
    ddata = {**json.loads(data), 'ip': ip}
    llg.debug(f"WID: {wid} JSON: {ddata}")
    dbh, c = get_db(wid)
    c.execute("""CREATE TABLE IF NOT EXISTS data (
                    timedate text primary key, ip text,
                    temperature real, humidity real, pressure real,
                    voltage real, voltagesun real, message text)""")
    c.execute("CREATE TABLE IF NOT EXISTS params (name text primary key, value text)")
    ddata = {f: ddata.get(f) for f in DB_FIELDS}
    # Use server UTC time — device RTC drifts
    ddata["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""INSERT OR REPLACE INTO data
                    (timedate,ip,temperature,humidity,pressure,voltage,voltagesun,message)
                    VALUES (:ts,:ip,:t,:h,:p,:v,:vs,:m)""", ddata)
    dbh.commit(); dbh.close()

if __name__ == "__main__":
    main()

# vim: ai ts=4 sts=4 et sw=4 ft=python
