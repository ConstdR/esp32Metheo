# Boot file - executed on every boot (including wake from deepsleep)
import esp, time, machine, _thread, network, ntptime, random, json, socket
from espconf import *

esp.osdebug(None)

HTTP_RESPONSE = b"HTTP/1.0 200 OK\r\nContent-Length: %d\r\n\r\n%s"
CFG_DEFAULTS  = {'essid': 'essid', 'pswd': 'pswd', 'tz': TZ, 'message': "No config", 'ap': 0}
cfg = CFG_DEFAULTS.copy()
_hextobyte_cache = None

# -- helpers -----------------------------------------------------------------

def blink(freq=0):
    pled = machine.Pin(LED_PIN, machine.Pin.OUT)
    try:    freq = float(freq)
    except: freq = 0
    while True:
        pled.value(0 if pled.value() else 1);
        time.sleep(random.randint(0, 50) / 100 if freq == 0 else 1 / (freq * 2))

def unquote(string):
    "unquote('abc%20de+f') -> b'abc de f'."
    global _hextobyte_cache
    if not string: return b''
    if isinstance(string, str): string = string.replace('+', ' ').encode('utf-8')
    bits = string.split(b'%')
    if len(bits) == 1: return string
    if _hextobyte_cache is None: _hextobyte_cache = {}
    res = [bits[0]]
    for item in bits[1:]:
        try:
            code = item[:2]
            if code not in _hextobyte_cache: _hextobyte_cache[code] = bytes([int(code, 16)])
            res += [_hextobyte_cache[code], item[2:]]
        except KeyError:
            res += [b'%', item]
    return b''.join(res)

# -- config / network --------------------------------------------------------

def get_cfg():
    global cfg
    try:
        with open(CFG_NAME) as fh: cfg = json.loads(fh.read())
        if not machine.Pin(AP_PIN, machine.Pin.IN).value():
            print("AP PIN → AP mode"); cfg['ap'] = 1
        cfg['message'] = "Config ready."
    except Exception as e:
        print(f"No {CFG_NAME}: {e} → AP mode"); cfg = CFG_DEFAULTS.copy(); cfg['ap'] = 1
    return cfg

def connect(essid, pswd):
    wlan = network.WLAN(network.STA_IF); wlan.active(True); wlan.connect(essid, pswd)
    for i in range(CONNECT_WAIT):
        if wlan.isconnected():
            print('Network:', wlan.ifconfig())
            try:    ntptime.settime(); print("Time synced")
            except Exception as e: print("NTP fail:", e)
            return
        print(f"{i} ...", end='\r'); time.sleep(1)

def _test_wifi(essid, pswd):
    "Try connecting; return (wlan, connected)."
    w = network.WLAN(network.STA_IF); w.active(True); w.connect(essid, pswd)
    for i in range(CONNECT_WAIT):
        time.sleep(1); print(f"{i} ...", end='\r')
        if w.isconnected(): return w, True
    return w, False

# -- AP / web ----------------------------------------------------------------

def process_request(request):
    try:
        parts = request.decode().split('\n')[0].strip().split(' ')
        lst   = parts[1].split('?')[1].split('&')
        res   = {p.split('=')[0]: unquote(p.split('=')[1]).decode() for p in lst}
        if res.get('reboot') == 'on': print("REBOOT!"); machine.reset()
        return res
    except Exception as e:
        print(f"Request parse error: {e}"); return None

def web_page(conf):
    html  = ('<!doctype html><html><link rel="icon" href="data:;base64,Qk0eAAAAAAAAABoAAAAMAAAAAQABAAEAGAAAAP8A">'
             '<body><h1>%s</h1>' % ap.config('essid'))
    html += ('<form action="/" method="get">'
             '<p>ESSID:<input name="essid" type=text value="%(essid)s"></p>'
             '<p>Password:<input name="pswd" type=text value="%(pswd)s"></p>'
             '<p>Time: UTC + <input name="tz" type=text value="%(tz)s"></p>'
             '<p><input type="submit" value="Submit">') % conf
    html += '<input type="checkbox" name="reboot">Reboot' if not conf['ap'] else ''
    html += '</p></form><hr>%(message)s</body></html>' % conf
    return html

def start_ap():
    "Start WiFi AP, serve config page until valid credentials are saved."
    global cfg, ap
    ap = network.WLAN(network.AP_IF); ap.active(True)
    while not ap.active(): time.sleep(1)
    print(f"AP active: {ap.config('essid')} {ap.ifconfig()}")
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', 80)); s.listen(5)
    while True:
        try:
            conn, addr = s.accept()
            print(f"Connection from {addr}")
            res = process_request(conn.recv(1024))
            if not res:
                cfg = get_cfg(); html = web_page(cfg)
            else:
                res['message'] = cfg['message']
                w, ok = _test_wifi(res['essid'], res['pswd'])
                if not ok:
                    res['message'] = f"{res['essid']} connection failed!"; res['ap'] = 1
                else:
                    res['message'] = f"{res['essid']} connected."; res['ap'] = 0
                    try:
                        with open(CFG_NAME, 'w') as fh: fh.write(json.dumps(res))
                        res['message'] += ' Settings stored. Switch off AP and reboot.'
                    except Exception as e:
                        res['message'] += f' Store error: {e}'; print(f"Store cfg error: {e}")
                w.active(False); html = web_page(res)
            conn.send(HTTP_RESPONSE % (len(html), html)); conn.close()
        except Exception as e: print(f"Request exception: {e}")

# -- entry point -------------------------------------------------------------

_thread.start_new_thread(blink, ())
try:
    cfg = get_cfg()
    if cfg.get('ap'): start_ap()
    else: connect(cfg['essid'], cfg['pswd'])
except Exception as e: print(f"Boot error: {e}")
