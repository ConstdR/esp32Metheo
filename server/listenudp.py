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
last = {}        # {topic: last_value} — for deduplication of identical packets
last_store = {}  # {device_id: timestamp} — to prevent duplicate readings within time window
dev_names = {}   # {device_id: name} — cached device names for logging
DEDUP_WINDOW = 30  # seconds — ignore readings from same device within this window
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
    if last.get(pkt.topic) == pkt.value:
        llg.debug(f"dup skipped: {pkt.topic}\t\t{pkt.addr}")
        return
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
    parsed = json.loads(data)
    dbh, c = get_db(wid)
    for k, v in parsed.items():
        c.execute("INSERT OR REPLACE INTO params (name, value) VALUES (?,?)", (k, v))
    dbh.commit()
    # Load device name from DB and cache it
    try:
        row = c.execute("SELECT value FROM params WHERE name='name'").fetchone()
        if row: dev_names[wid] = row[0]
    except Exception:
        pass
    dbh.close()
    name = dev_names.get(wid)
    label = f"{wid} ({name})" if name else wid
    llg.info(f"CONF: {label} {parsed}")

def store(wid, data, ip):
    """Save sensor reading to the 'data' table.

    Timestamp logic:
      - If device sends 'ts' field (MicroPython batch upload) → use device ts.
        Broadcast duplicates are handled by INSERT OR REPLACE (same ts = same PK).
      - If no 'ts' (ESP-IDF firmware) → use server UTC time.
        Dedup window prevents broadcast duplicates from creating multiple rows.
    Creates tables on first insert for new devices."""
    ddata = {**json.loads(data), 'ip': ip}

    # Determine timestamp: device ts (MicroPython) or server UTC (ESP-IDF)
    device_ts = ddata.get('ts')
    if device_ts:
        # MicroPython: use device timestamp, normalize T→space
        ddata['ts'] = device_ts.replace("T", " ")
    else:
        # ESP-IDF: no ts in payload, use server time with dedup window
        now = datetime.now(timezone.utc)
        prev = last_store.get(wid)
        if prev and (now - prev).total_seconds() < DEDUP_WINDOW:
            llg.debug(f"WID: {wid} skipped (dedup {DEDUP_WINDOW}s)")
            return
        last_store[wid] = now
        ddata['ts'] = now.strftime("%Y-%m-%d %H:%M:%S")

    # Get device name for logging (from cache or DB)
    name = dev_names.get(wid)
    if not name:
        try:
            dbh0, c0 = get_db(wid)
            row0 = c0.execute("SELECT value FROM params WHERE name='name'").fetchone()
            if row0: name = dev_names[wid] = row0[0]
            dbh0.close()
        except Exception:
            pass
    label = f"{wid} ({name})" if name else wid
    llg.info(f"WID: {label} JSON: {ddata}")
    dbh, c = get_db(wid)
    c.execute("""CREATE TABLE IF NOT EXISTS data (
                    timedate text primary key, ip text,
                    temperature real, humidity real, pressure real,
                    voltage real, voltagesun real, message text)""")
    c.execute("CREATE TABLE IF NOT EXISTS params (name text primary key, value text)")
    # Save device MAC as param (useful for display and identification)
    c.execute("INSERT OR REPLACE INTO params (name, value) VALUES ('device_id', ?)", (wid,))
    ddata = {f: ddata.get(f) for f in DB_FIELDS}
    c.execute("""INSERT OR REPLACE INTO data
                    (timedate,ip,temperature,humidity,pressure,voltage,voltagesun,message)
                    VALUES (:ts,:ip,:t,:h,:p,:v,:vs,:m)""", ddata)
    dbh.commit(); dbh.close()

if __name__ == "__main__":
    main()

# vim: ai ts=4 sts=4 et sw=4 ft=python
