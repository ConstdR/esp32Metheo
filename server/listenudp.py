#!/usr/bin/env python3

'''
MQTT/UDP listen and store data
'''

# will work even if package is not installed
import argparse
import logging
import configparser
import re
import sqlite3
import json
import os

import mqttudp.engine as me

llg = logging.getLogger(__name__)
args = None
cfg = None

last = {}

def main():
    global args, cfg
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', dest='config', default='config.cfg',
                    help="Config file. Default: config.cfg")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)
    cfg = config['listener']
    llg.setLevel(cfg['debug'])

    me.listen(recv_packet)

def recv_packet(pkt):
    global last
    if pkt.ptype != me.PacketType['Publish']:
        # not publish msg
        return
    if last.__contains__(pkt.topic) and last[pkt.topic] == pkt.value:
        # duplicate
        return
    last[pkt.topic] = pkt.value
    llg.debug( pkt.topic+"="+pkt.value+ "\t\t" + str(pkt.addr) )

    r = re.match(r'^weather/+([^/]*)/config$', pkt.topic)
    if r and r.group(1):
        try:
            llg.info("Config from %s : %s" % (r.group(1), pkt.value))
            store_conf(r.group(1), pkt.value)
        except Exception as e:
            llg.error("Config store error: %s" % e)

    r = re.match(r'^weather/+([^/]*)$', pkt.topic)
    if r and r.group(1):
        llg.debug("Weather id: %s" % r.group(1))
        try:
            store(r.group(1), pkt.value, str(pkt.addr))
        except Exception as e:
            llg.error("Store error: %s" % e)

def store_conf(wid, data):
    dbname = cfg['dbdir']+'/'+wid+'.sqlite3'
    ddata  = json.loads(data)
    dbh = sqlite3.connect(dbname)
    c = dbh.cursor()
    for key, value in ddata.items():
        c.execute("insert or replace into params (name, value) values (?,?)", (key, value))
    dbh.commit()
    dbh.close()

def store(wid, data, ip):
    ddata  = json.loads(data)
    ddata['ip'] = ip
    llg.debug("WID: %s JSON: %s" % (wid, ddata))
    dbname = cfg['dbdir']+'/'+wid+'.sqlite3'
    dbh = sqlite3.connect(dbname)
    c = dbh.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS data ( timedate text primary key, ip text,
                                        temperature real, humidity real,
                                        pressure real, voltage int, voltagesun int,
                                        azimuth int, altitude int,
                                        wake int, message text)""")
    dbh.commit()
    c.execute("CREATE TABLE IF NOT EXISTS params (name text primary key, value text)")
    dbh.commit()
    fields = ['ts', 'ip', 't', 'h', 'p', 'v', 'vs', 'az', 'alt', 'w', 'm']
    for f in fields:
        if f not in ddata:
            ddata[f] = None
    c.execute("""insert or replace into data (timedate, ip, temperature, humidity,
                        pressure, voltage, voltagesun, azimuth, altitude,
                        wake, message) 
                        values (:ts, :ip, :t, :h,
                        :p, :v, :vs, :az, :alt, 
                        :w, :m)""", ddata)
    dbh.commit()
    dbh.close()

if __name__ == "__main__":
    main()
    
# vim: ai ts=4 sts=4 et sw=4 ft=python
