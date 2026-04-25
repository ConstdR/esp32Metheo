#!/usr/bin/env python3
# encoding: utf-8
"""
Web interface for ESP32 Weather Station.

Serves:
  /              — index page with all sensors as cards
  /graph/<id>    — detail page with current values + history graphs
  /csv/<id>      — CSV data for dygraph (used by graph.html via XHR)
  /id/<id>       — POST endpoint for MicroPython bulk data upload (legacy)

Each sensor has its own SQLite database (<device_id>.sqlite3) with:
  - "data" table:   timestamped readings (temperature, humidity, pressure, voltage)
  - "params" table: device config key-value pairs (fw, sensor type, gpio, etc.)

Voltage normalization:
  - ESP-IDF firmware sends calibrated voltages → displayed as-is
  - MicroPython firmware sends raw ADC values → normalized using max-based scaling
"""

import argparse, configparser, logging, os, re, sqlite3, sys
from contextlib import contextmanager
from datetime import datetime, timezone
from multiprocessing import Process

import jinja2
from aiohttp import web
from dateutil.relativedelta import relativedelta
import listenudp

DEF_RANGE = 7            # default date range for graphs (days)
VOLT_WIN  = "-60 day"    # window for max voltage calculation
VBAT, VSOL = 4.2, 6.0   # nominal max voltages for MicroPython normalization

lg  = logging.getLogger(__name__)
env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"))

# -- helpers -----------------------------------------------------------------

def dict_factory(cur, row):
    """SQLite row factory: returns dict with column names as keys.
    Replaces string "None" with empty string for cleaner templates."""
    return {c[0]: (row[i] if row[i] != "None" else "") for i, c in enumerate(cur.description)}

@contextmanager
def get_db(path):
    """Context manager for SQLite connection with dict row factory."""
    dbh = sqlite3.connect(path)
    dbh.row_factory = dict_factory
    try:    yield dbh
    finally: dbh.close()

def db_path(cfg, sid):  return os.path.join(cfg["dbdir"], f"{sid}.sqlite3")
def cfg(req):           return req.app["cfg"]
def sid(req):           return req.match_info["id"]
def tmpl(name):         return env.get_template(name)
def html(body):         return web.Response(content_type="text/html", charset="utf-8", body=body)

def get_range(request):
    """Parse date range from query string '2024-01-01 - 2024-01-07',
    or default to last DEF_RANGE days."""
    raw = request.query.get("daterange", "")
    m   = re.match(r"(\d{4}-\d\d-\d\d).-.(\d{4}-\d\d-\d\d)", raw) if raw else None
    if m: return m.groups()
    now = datetime.now()
    return ((now - relativedelta(days=DEF_RANGE)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))

def max_voltages(dbh):
    """Get peak voltage and solar voltage from last VOLT_WIN period.
    Used for MicroPython normalization (raw ADC → estimated real voltage)."""
    return dbh.execute(f"SELECT max(voltage) AS mv, max(voltagesun) AS mvs FROM data "
                       f"WHERE timedate > datetime(date('now'), '{VOLT_WIN}')").fetchone()

def time_ago(utc_str):
    """Convert UTC timestamp string to human-readable '5m ago' / '2h 30m ago' / '1d 3h ago'.
    Returns (ago_string, delta_seconds). Used for index cards and offline detection."""
    try:
        last_dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
        mins = int(delta / 60)
        if mins < 60:
            return f"{mins}m ago", delta
        elif mins < 1440:
            return f"{mins // 60}h {mins % 60}m ago", delta
        else:
            return f"{mins // 1440}d {(mins % 1440) // 60}h ago", delta
    except Exception:
        return "", 0

def weather_forecast(db):
    """Simple weather forecast based on pressure and humidity trends over 6 hours.
    Returns dict with 'icon', 'text', and 'detail' (rate of change)."""
    try:
        # Get pressure and humidity from 6 hours ago and now
        now = db.execute("""SELECT pressure, humidity FROM data
                           WHERE pressure IS NOT NULL AND pressure != 'None' AND pressure > 0
                           ORDER BY timedate DESC LIMIT 1""").fetchone()
        ago = db.execute("""SELECT avg(pressure) AS pressure, avg(humidity) AS humidity FROM data
                           WHERE pressure IS NOT NULL AND pressure != 'None' AND pressure > 0
                             AND timedate > datetime('now', '-6.5 hours')
                             AND timedate < datetime('now', '-5.5 hours')""").fetchone()
        if not now or not ago or not ago["pressure"]:
            return {"icon": "", "text": "", "detail": ""}

        p_now, p_ago = now["pressure"], ago["pressure"]
        h_now, h_ago = now["humidity"] or 0, ago["humidity"] or 0
        p_diff = p_now - p_ago          # positive = rising
        p_rate = p_diff / 6.0           # hPa per hour
        h_diff = h_now - h_ago          # positive = getting wetter

        # Forecast logic
        if p_rate < -1.0:
            # Rapid drop > 6 hPa in 6 hours
            icon, text = "⛈", "Storm warning"
        elif p_rate < -0.5 and h_diff > 5:
            # Falling pressure + rising humidity
            icon, text = "🌧", "Rain likely"
        elif p_rate < -0.3:
            # Moderate drop
            icon, text = "↘", "Worsening"
        elif p_rate > 0.5:
            # Rising fast
            icon, text = "☀", "Clear"
        elif p_rate > 0.2:
            # Rising moderately
            icon, text = "↗", "Improving"
        else:
            # Stable
            icon, text = "●", "Steady"

        detail = f"{p_diff:+.1f} hPa/6h ({p_rate:+.2f}/h)"
        return {"icon": icon, "text": text, "detail": detail}
    except Exception:
        return {"icon": "", "text": "", "detail": ""}

def brief_data(fname):
    """Load latest reading + device params for a single sensor.
    Returns a dict with all fields needed by index.html and graph.html:
    temperature, humidity, pressure, voltage (v/vs), timing (ago, offline),
    device config (fw, sensor, gpio pins), and display helpers (low_bat, period)."""
    with get_db(fname) as db:
        # Latest sensor reading (local time for display, UTC for age calc)
        row   = db.execute("""SELECT round(temperature,1) AS temperature,
                      round(humidity,1) AS humidity,
                      round(pressure,1) AS pressure,
                      round(voltage,2) AS voltage,
                      round(voltagesun,2) AS voltagesun,
                      ip, message, datetime(timedate,'localtime') AS tztime,
                      timedate
                      FROM data ORDER BY timedate DESC LIMIT 1""").fetchone()
        maxv  = max_voltages(db)
        # Device config params (fw type, sensor, gpio, thresholds, etc.)
        params = {r["name"]: r["value"] for r in db.execute("SELECT name,value FROM params").fetchall()}
        # Weather forecast from pressure/humidity trends
        forecast = weather_forecast(db)

    # Voltage: ESP-IDF sends real volts, MicroPython needs normalization
    row["v"]   = round(row["voltage"]    / maxv["mv"]  * VBAT, 2) if maxv["mv"]  and row["voltage"]    and params.get("fw") != "espidf" else round(row["voltage"] or 0, 2)
    row["vs"]  = round(row["voltagesun"] / maxv["mvs"] * VSOL, 2) if maxv["mvs"] and row["voltagesun"] and params.get("fw") != "espidf" else round(row["voltagesun"] or 0, 2)
    row["mvs"] = maxv["mvs"]
    row["raw_volts"] = params.get("fw") == "espidf"

    # Merge device params into row (fw, sensor, sleep, led, i2c_*, etc.)
    row.update(params)
    row.setdefault("name", "_new_")

    # Calculate sleep period in seconds (for refresh timer and offline detection)
    if params.get("fw") == "espidf":
        # ESP-IDF: sleep value is in minutes (from menuconfig)
        sleep_sec = int(row.get("sleep", 15)) * 60
    else:
        # MicroPython: sleep value is in milliseconds
        sleep_ms = int(row.get("sleep", 900_000))
        sleep_sec = sleep_ms / 1000 if sleep_ms > 1000 else sleep_ms
    row["period"] = sleep_sec

    # Time since last reading + offline detection (>2.5× sleep = offline)
    row["ago"], delta = time_ago(row.get("timedate", ""))
    row["offline"] = delta > sleep_sec * 2.5 if delta else False

    # Low battery flag: voltage below threshold from device config (lowb in mV)
    try:
        lowb_v = int(row.get("lowb", 0)) / 1000.0
        row["low_bat"] = lowb_v > 0 and row["v"] > 0 and row["v"] < lowb_v
    except (ValueError, TypeError):
        row["low_bat"] = False

    # Color classes and indicator symbols for readings (● = ok, ▲ = high, ▼ = low)
    t = row["temperature"] or 0
    row["temp_color"] = "cold" if t < 0 else ("hot" if t > 25 else "ok")
    row["temp_sym"]   = "▼" if t < 0 else ("▲" if t > 25 else "●")

    h = row["humidity"] or 0
    row["hum_color"] = "ok" if 40 <= h <= 60 else ("warn" if 20 <= h <= 80 else "bad")
    row["hum_sym"]   = "●" if 40 <= h <= 60 else ("▼" if h < 40 else "▲")

    v = row["v"] or 0
    row["bat_color"] = "ok" if v > 3.8 else ("warn" if v > 3.5 else "bad")
    row["bat_sym"]   = "●" if v > 3.8 else "▼"

    vs = row["vs"] or 0
    row["sol_color"] = "ok" if vs > 0 else "off"
    row["sol_sym"]   = "●" if vs > 0 else "○"

    # Weather forecast (only for sensors with pressure)
    row["forecast"] = forecast

    return row

# -- route handlers ----------------------------------------------------------

async def favicon(request):
    res = web.FileResponse("static/favicon.ico")
    res.headers["Cache-Control"] = "max-age=10000"
    return res

async def store(request):
    """Legacy endpoint for MicroPython bulk upload: POST /id/<device_id>
    Body: {"measures": ["2024-01-01 12:00:00,21.5,45,1013,3.8,0,msg", ...]}"""
    measures = (await request.json())["measures"]
    path = db_path(cfg(request), sid(request))
    lg.info(f"Post {len(measures)} rows {measures[0].split(',')[0]} → {measures[-1].split(',')[0]} UTC")
    with sqlite3.connect(path) as db:
        for m in measures:
            vals = m.split(","); vals.insert(1, request.remote)
            try:    db.execute("INSERT OR REPLACE INTO data VALUES(?,?,?,?,?,?,?,?)", vals)
            except Exception as e: lg.error(f"Insert error: {e} | row: {m}")
        db.commit()
    return web.Response(text="OK")

async def graph(request):
    """Detail page for a single sensor: current values + history graphs.
    Supports ?rename=NewName to rename the sensor.
    Supports ?daterange=YYYY-MM-DD - YYYY-MM-DD to select graph period."""
    path = db_path(cfg(request), sid(request))
    if not os.path.isfile(path): raise web.HTTPNotFound(text="Not here.")
    if "rename" in request.query:
        new_name = request.query["rename"]
        with sqlite3.connect(path) as db:
            old = db.execute("SELECT value FROM params WHERE name='name'").fetchone()
            old_name = old[0] if old else "_unnamed_"
            db.execute("INSERT OR REPLACE INTO params VALUES(?,?)", ("name", new_name))
            db.commit()
        lg.info(f"Rename {sid(request)}: '{old_name}' → '{new_name}'")
        raise web.HTTPFound(location=f"/graph/{sid(request)}")
    info = brief_data(path)
    # refreshtime = half the sleep period (page reloads between measurements)
    info |= {"id": sid(request), "refreshtime": int(info["period"] / 2)}
    info["startdate"], info["enddate"] = get_range(request)
    return html(tmpl("graph.html").render(info))

async def index(request):
    """Main page: card for each sensor with current readings."""
    sensors = {}
    for name in os.listdir(cfg(request)["dbdir"]):
        if not name.endswith(".sqlite3"): continue
        try:    sensors[name.removesuffix(".sqlite3")] = brief_data(os.path.join(cfg(request)["dbdir"], name))
        except Exception as e: lg.error(f"Bad data in {name}: {e}")
    # Refresh = half of shortest sleep period among sensors (default 450s if none)
    refresh = min((s["period"] for s in sensors.values() if s.get("period")), default=900) // 2
    return html(tmpl("index.html").render({"sensors": sensors, "refreshtime": max(refresh, 120)}))

async def csv_get(request):
    """CSV data for dygraph charts. Columns: time, temperature, humidity,
    pressure, voltage, solar_voltage. Voltage is normalized for MicroPython
    devices, raw for ESP-IDF."""
    startdate, enddate = get_range(request)
    with get_db(db_path(cfg(request), sid(request))) as db:
        params = {r["name"]: r["value"] for r in db.execute("SELECT name,value FROM params").fetchall()}
        if params.get("fw") == "espidf":
            v, vs = 1, 1  # raw volts, no normalization needed
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
    # Start UDP listener in a separate process
    Process(target=listenudp.main).start()
    # Start web server
    app = web.Application()
    app["cfg"] = c
    app.add_routes([web.get("/", index), web.get(r"/csv/{id}", csv_get),
                    web.get(r"/graph/{id}", graph), web.post(r"/id/{id}", store),
                    web.get("/favicon.ico", favicon), web.static("/static", "static")])
    lg.warning(f"Web running on http://{c['host']}:{c['port']}")
    web.run_app(app, host=c["host"], port=int(c["port"]))

if __name__ == "__main__":
    try:    main()
    except KeyboardInterrupt: print("Interrupted"); sys.exit(0)

# vim: ai ts=4 sts=4 et sw=4 ft=python
