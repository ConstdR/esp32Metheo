#!/usr/bin/env python3
"""MQTT/UDP listen and store data."""
import argparse, configparser, logging, re, sqlite3, json
import mqttudp.engine as me

llg  = logging.getLogger(__name__)
last = {}
cfg  = None

DB_FIELDS = ['ts','ip','t','h','p','v','vs','az','alt','w','m']

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
    global last
    if pkt.ptype != me.PacketType['Publish']:           return
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
    dbh, c = get_db(wid)
    for k, v in json.loads(data).items():
        c.execute("INSERT OR REPLACE INTO params (name, value) VALUES (?,?)", (k, v))
    dbh.commit(); dbh.close()

def store(wid, data, ip):
    ddata = {**json.loads(data), 'ip': ip}
    llg.debug(f"WID: {wid} JSON: {ddata}")
    dbh, c = get_db(wid)
    c.execute("""CREATE TABLE IF NOT EXISTS data (
                    timedate text primary key, ip text,
                    temperature real, humidity real, pressure real,
                    voltage int, voltagesun int, azimuth int, altitude int,
                    wake int, message text)""")
    c.execute("CREATE TABLE IF NOT EXISTS params (name text primary key, value text)")
    ddata = {f: ddata.get(f) for f in DB_FIELDS}
    c.execute("""INSERT OR REPLACE INTO data
                    (timedate,ip,temperature,humidity,pressure,voltage,voltagesun,azimuth,altitude,wake,message)
                    VALUES (:ts,:ip,:t,:h,:p,:v,:vs,:az,:alt,:w,:m)""", ddata)
    dbh.commit(); dbh.close()

if __name__ == "__main__":
    main()

# vim: ai ts=4 sts=4 et sw=4 ft=python
