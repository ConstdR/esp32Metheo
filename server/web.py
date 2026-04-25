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

# Thresholds loaded from config.cfg [thresholds] section at startup.
# All numeric values are stored as floats; keys match config option names.
TH = {}

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
    or default to last default_range_days days."""
    raw = request.query.get("daterange", "")
    m   = re.match(r"(\d{4}-\d\d-\d\d).-.(\d{4}-\d\d-\d\d)", raw) if raw else None
    if m: return m.groups()
    now = datetime.now()
    return ((now - relativedelta(days=TH["default_range_days"])).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))

def max_voltages(dbh):
    """Get peak voltage and solar voltage from last volt_max_window_days period.
    Used for MicroPython normalization (raw ADC → estimated real voltage)."""
    return dbh.execute(f"SELECT max(voltage) AS mv, max(voltagesun) AS mvs FROM data "
                       f"WHERE timedate > datetime(date('now'), '-{TH["volt_max_window_days"]} day')").fetchone()

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
        if p_rate < TH["forecast_storm"]:
            icon, text = "⛈", "Storm warning"
        elif p_rate < TH["forecast_storm"] / 2 and h_diff > TH["forecast_rain_hum_diff"]:
            icon, text = "🌧", "Rain likely"
        elif p_rate < TH["forecast_worsening"]:
            icon, text = "↘", "Worsening"
        elif p_rate > TH["forecast_clear"]:
            icon, text = "☀", "Clear"
        elif p_rate > TH["forecast_improving"]:
            icon, text = "↗", "Improving"
        else:
            icon, text = "●", "Steady"

        detail = f"{p_diff:+.1f} hPa/6h ({p_rate:+.2f}/h)"
        return {"icon": icon, "text": text, "detail": detail}
    except Exception:
        return {"icon": "", "text": "", "detail": ""}

# -- data processing helpers -------------------------------------------------

def is_espidf(params):
    """True if device runs ESP-IDF firmware (vs legacy MicroPython)."""
    return params.get("fw") == "espidf"

def norm_voltage(raw, peak, nominal, espidf):
    """Normalize raw voltage to volts.
    ESP-IDF sends calibrated volts → return as-is. MicroPython sends raw ADC →
    scale by peak-based factor. Falls back to raw value if peak unavailable."""
    raw = raw or 0
    if espidf or not peak or not raw:
        return round(raw, 2)
    return round(raw / peak * nominal, 2)

def sleep_seconds(params):
    """Sleep period in seconds. ESP-IDF stores minutes, MicroPython stores ms."""
    if is_espidf(params):
        return int(params.get("sleep", 15)) * 60
    sleep_ms = int(params.get("sleep", 900_000))
    return sleep_ms / 1000 if sleep_ms > 1000 else sleep_ms

def is_low_bat(v, params):
    """True if voltage below device-configured 'lowb' threshold (stored in mV)."""
    try:
        lowb_v = int(params.get("lowb", 0)) / 1000.0
        return lowb_v > 0 and v > 0 and v < lowb_v
    except (ValueError, TypeError):
        return False

# Status helpers — each returns (color_class, symbol) for templates.
# ● = ok, ▲ = high, ▼ = low, ○ = inactive

def temp_status(t):
    if t < TH["temp_cold"]: return "cold", "▼"
    if t > TH["temp_hot"]:  return "hot",  "▲"
    return "ok", "●"

def hum_status(h):
    if TH["hum_ok_low"] <= h <= TH["hum_ok_high"]:
        return "ok", "●"
    sym   = "▼" if h < TH["hum_ok_low"] else "▲"
    color = "warn" if TH["hum_dry_low"] <= h <= TH["hum_wet_high"] else "bad"
    return color, sym

def bat_status(v):
    if v > TH["bat_ok"]:   return "ok",   "●"
    if v > TH["bat_warn"]: return "warn", "▼"
    return "bad", "▼"

def sol_status(vs):
    return ("ok", "●") if vs > 0 else ("off", "○")

def device_summary(d):
    """Build HTML summary string for graph footer (hardware/firmware config + IP + ID).
    d is the merged dict (latest data row + device params from brief_data()).
    Mirrors the original {% if fw %}{% endif %} template block as one rendered string."""
    parts = []
    if d.get("fw"):
        parts.append(f"<b>{d['fw']}</b>")
        parts.append(f"sensor: {d.get('sensor', '')}")
        parts.append(f"sleep: {d.get('sleep', '')}min")
        led = f"LED: GPIO{d.get('led', '')}"
        if d.get("led_inv") == "1": led += " (inv)"
        parts.append(led)
        parts.append(f"I2C: SDA={d.get('i2c_sda', '')} SCL={d.get('i2c_scl', '')}")
        bat = f"BAT: GPIO{d.get('bat_gpio', '')}"
        if d.get("lowb"):
            try:
                bat += f" low&lt;{round(int(d['lowb']) / 1000, 1)}V"
            except (ValueError, TypeError):
                pass
        parts.append(bat)
        if d.get("solar") == "1": parts.append(f"SOL: GPIO{d.get('sol_gpio', '')}")
        if d.get("rst"):          parts.append(f"rst: {d['rst']}")
    parts.append(f"IP: {d.get('ip', '')}")
    if d.get("device_id"):
        parts.append(f"ID: {d['device_id']}")
    return " · ".join(parts)

# -- main data assembler -----------------------------------------------------

def brief_data(fname):
    """Load latest reading + device params for a single sensor.
    Returns a dict with all fields needed by index.html and graph.html:
    temperature, humidity, pressure, voltage (v/vs), timing (ago, offline),
    device config (fw, sensor, gpio pins), and display helpers (low_bat, period)."""
    with get_db(fname) as db:
        # Latest sensor reading (local time for display, UTC for age calc)
        row = db.execute("""SELECT round(temperature,1) AS temperature,
                                   round(humidity,1) AS humidity,
                                   round(pressure,1) AS pressure,
                                   round(voltage,2) AS voltage,
                                   round(voltagesun,2) AS voltagesun,
                                   ip, message, datetime(timedate,'localtime') AS tztime,
                                   timedate
                            FROM data ORDER BY timedate DESC LIMIT 1""").fetchone()
        maxv     = max_voltages(db)
        params   = {r["name"]: r["value"] for r in db.execute("SELECT name,value FROM params").fetchall()}
        forecast = weather_forecast(db)

    espidf = is_espidf(params)

    # Voltage normalization
    row["v"]         = norm_voltage(row["voltage"],    maxv["mv"],  TH["nominal_battery_v"], espidf)
    row["vs"]        = norm_voltage(row["voltagesun"], maxv["mvs"], TH["nominal_solar_v"],   espidf)
    row["mvs"]       = maxv["mvs"]
    row["raw_volts"] = espidf

    # Merge device params into row (fw, sensor, sleep, led, i2c_*, etc.)
    row.update(params)
    row.setdefault("name", "_new_")

    # Timing: sleep period, age, offline (>2.5× sleep)
    row["period"]     = sleep_seconds(params)
    row["ago"], delta = time_ago(row.get("timedate", ""))
    row["offline"]    = delta > row["period"] * 2.5 if delta else False

    # Status flags & display tuples
    row["low_bat"]                     = is_low_bat(row["v"], params)
    row["temp_color"], row["temp_sym"] = temp_status(row["temperature"] or 0)
    row["hum_color"],  row["hum_sym"]  = hum_status(row["humidity"] or 0)
    row["bat_color"],  row["bat_sym"]  = bat_status(row["v"] or 0)
    row["sol_color"],  row["sol_sym"]  = sol_status(row["vs"] or 0)
    row["forecast"]                    = forecast
    row["device_summary"]              = device_summary(row)

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
    info |= {"id": sid(request), "refreshtime": int(info["period"] / 2), "th": TH}
    info["startdate"], info["enddate"] = get_range(request)
    info["s"] = info
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
        if is_espidf(params):
            v, vs = 1, 1  # raw volts, no normalization needed
        else:
            maxv = max_voltages(db)
            v  = TH["nominal_battery_v"] / maxv["mv"]  if maxv["mv"]  else 0
            vs = TH["nominal_solar_v"] / maxv["mvs"] if maxv["mvs"] else 0
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
    global TH
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default="config.cfg")
    args   = parser.parse_args()
    config = configparser.ConfigParser(); config.read(args.config)
    c = config["default"]
    # Load all numeric thresholds from [thresholds] section
    if "thresholds" in config:
        for k, v in config["thresholds"].items():
            try:    TH[k] = float(v)
            except ValueError: TH[k] = v
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
