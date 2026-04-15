#!/usr/bin/env python3
# encoding: utf-8

import argparse, configparser, logging, os, re, sqlite3, sys
from contextlib import contextmanager
from datetime import datetime
from multiprocessing import Process

import jinja2
from aiohttp import web
from dateutil.relativedelta import relativedelta
import listenudp

DEF_RANGE = 7
VOLT_WIN  = "-60 day"
VBAT, VSOL = 4.2, 6.0

lg  = logging.getLogger(__name__)
env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"))

# -- helpers -----------------------------------------------------------------

def dict_factory(cur, row):
    return {c[0]: (row[i] if row[i] != "None" else "") for i, c in enumerate(cur.description)}

@contextmanager
def get_db(path):
    dbh = sqlite3.connect(path)
    dbh.row_factory = dict_factory
    try:    yield dbh
    finally: dbh.close()

def db_path(cfg, sid):  return os.path.join(cfg["dbdir"], f"{sid}.sqlite3")
def cfg(req):           return req.app["cfg"]
def sid(req):           return req.match_info["id"]
def tmpl(name):         return env.get_template(name)
def html(body):         return web.Response(content_type="text/html", charset="utf-8", body=body)

def ensure_db(path):
    if os.path.isfile(path): return
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE data (timedate text primary key, ip text,
                      temperature real, humidity real, pressure real,
                      voltage real, voltagesun real, message text)""")
        db.execute("CREATE TABLE params (name text primary key, value text)")
        db.commit()

def get_range(request):
    raw = request.query.get("daterange", "")
    m   = re.match(r"(\d{4}-\d\d-\d\d).-.(\d{4}-\d\d-\d\d)", raw) if raw else None
    if m: return m.groups()
    now = datetime.now()
    return ((now - relativedelta(days=DEF_RANGE)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))

def max_voltages(dbh):
    return dbh.execute(f"SELECT max(voltage) AS mv, max(voltagesun) AS mvs FROM data "
                       f"WHERE timedate > datetime(date('now'), '{VOLT_WIN}')").fetchone()

def brief_data(fname):
    with get_db(fname) as db:
        row   = db.execute("""SELECT round(temperature,1) AS temperature,
                      round(humidity,1) AS humidity,
                      round(pressure,1) AS pressure,
                      round(voltage,2) AS voltage,
                      round(voltagesun,2) AS voltagesun,
                      ip, message, datetime(timedate,'localtime') AS tztime
                      FROM data ORDER BY timedate DESC LIMIT 1""").fetchone()
        maxv  = max_voltages(db)
        params = {r["name"]: r["value"] for r in db.execute("SELECT name,value FROM params").fetchall()}

    row["v"]   = round(row["voltage"]    / maxv["mv"]  * VBAT, 2) if maxv["mv"]  and row["voltage"]    and params.get("fw") != "espidf" else round(row["voltage"] or 0, 2)
    row["vs"]  = round(row["voltagesun"] / maxv["mvs"] * VSOL, 2) if maxv["mvs"] and row["voltagesun"] and params.get("fw") != "espidf" else round(row["voltagesun"] or 0, 2)
    row["mvs"] = maxv["mvs"]
    row["raw_volts"] = params.get("fw") == "espidf"
    row.update(params)
    row.setdefault("name", "_new_")

    if params.get("fw") == "espidf":
        # sleep is in minutes from menuconfig
        sleep_sec = int(row.get("sleep", 15)) * 60
    else:
        # MicroPython: sleep in milliseconds
        sleep_ms = int(row.get("sleep", 900_000))
        sleep_sec = sleep_ms / 1000 if sleep_ms > 1000 else sleep_ms
        if int(row.get("fake_sleep", 0)):
            sleep_sec /= 10
    row["period"] = sleep_sec / 2
    return row

# -- route handlers ----------------------------------------------------------

async def favicon(request):
    res = web.FileResponse("static/favicon.ico")
    res.headers["Cache-Control"] = "max-age=10000"
    return res

async def store(request):
    measures = (await request.json())["measures"]
    path = db_path(cfg(request), sid(request))
    ensure_db(path)
    lg.info(f"Post {len(measures)} rows {measures[0].split(',')[0]} → {measures[-1].split(',')[0]} UTC")
    with sqlite3.connect(path) as db:
        for m in measures:
            vals = m.split(","); vals.insert(1, request.remote)
            try:    db.execute("INSERT OR REPLACE INTO data VALUES(?,?,?,?,?,?,?,?)", vals)
            except Exception as e: lg.error(f"Insert error: {e} | row: {m}")
        db.commit()
    return web.Response(text="OK")

async def graph(request):
    path = db_path(cfg(request), sid(request))
    if not os.path.isfile(path): raise web.HTTPNotFound(text="Not here.")
    if "rename" in request.query:
        with sqlite3.connect(path) as db:
            db.execute("INSERT OR REPLACE INTO params VALUES(?,?)", ("name", request.query["rename"]))
            db.commit()
        raise web.HTTPFound(location=f"/graph/{sid(request)}")
    info = brief_data(path)
    info |= {"id": sid(request), "refreshtime": int(info["period"] / 2)}
    info["startdate"], info["enddate"] = get_range(request)
    return html(tmpl("graph.html").render(info))

async def index(request):
    sensors = {}
    for name in os.listdir(cfg(request)["dbdir"]):
        if not name.endswith(".sqlite3"): continue
        try:    sensors[name.removesuffix(".sqlite3")] = brief_data(os.path.join(cfg(request)["dbdir"], name))
        except Exception as e: lg.error(f"Bad data in {name}: {e}")
    return html(tmpl("index.html").render({"sensors": sensors, "refreshtime": 450}))

async def csv_get(request):
    startdate, enddate = get_range(request)
    with get_db(db_path(cfg(request), sid(request))) as db:
        params = {r["name"]: r["value"] for r in db.execute("SELECT name,value FROM params").fetchall()}
        if params.get("fw") == "espidf":
            v, vs = 1, 1  # raw volts, no normalization
        else:
            maxv = max_voltages(db)
            v  = VBAT / maxv["mv"]  if maxv["mv"]  else 0
            vs = VSOL / maxv["mvs"] if maxv["mvs"] else 0
        rows = db.execute("""SELECT temperature, humidity, pressure,
                      voltage*? AS voltage, voltagesun*? AS voltagesun,
                      datetime(timedate,'localtime') AS tztime FROM data
                      WHERE timedate >= datetime(?,'localtime')
                        AND timedate <= datetime(datetime(?,'localtime'),'1 day')
                      ORDER BY timedate""", (v, vs, startdate, enddate)).fetchall()
    txt = "\n".join("{tztime},{temperature},{humidity},{pressure},{voltage},{voltagesun}".format(**r) for r in rows)
    return web.Response(text=txt + "\n", content_type="text/csv")

# -- startup -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default="config.cfg")
    args   = parser.parse_args()
    config = configparser.ConfigParser(); config.read(args.config)
    c = config["default"]
    logging.basicConfig(level=c["debug"],
                        format="%(asctime)s %(name)s.%(lineno)s %(levelname)s: %(message)s")
    Process(target=listenudp.main).start()
    app = web.Application()
    app["cfg"] = c
    app.add_routes([web.get("/", index), web.get(r"/csv/{id}", csv_get),
                    web.get(r"/graph/{id}", graph), web.post(r"/id/{id}", store),
                    web.get("/favicon.ico", favicon), web.static("/static", "static")])
    lg.error(f"Web running on http://{c['host']}:{c['port']}")
    web.run_app(app, host=c["host"], port=int(c["port"]))

if __name__ == "__main__":
    try:    main()
    except KeyboardInterrupt: print("Interrupted"); sys.exit(0)

# vim: ai ts=4 sts=4 et sw=4 ft=python
