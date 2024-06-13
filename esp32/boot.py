# This file is executed on every boot (including wake-boot from deepsleep)
import esp
import time, machine, _thread, network, ntptime, random, json, socket
from espconf import *

esp.osdebug(None)

HTTP_RESPONSE = b"""\
HTTP/1.0 200 OK
Content-Length: %d

%s
"""

cfg={'essid':'essid', 'pswd':'pswd', 'tz':TZ, 'message':"No config", 'ap':0}

def blink(freq=0):
    pled=machine.Pin(LED_PIN, machine.Pin.OUT)
    pf  =machine.Pin(M_PIN, machine.Pin.OUT)
    try: freq = float(freq)
    except: freq = 0
    print("Blinking %s" % freq)
    while True:
        pled.value( 0 if pled.value() else 1 )
        pf.value(pled.value())
        time.sleep( random.randint(0,50)/100 if freq==0 else 1/(freq*2) )

def get_cfg():
    global cfg
    try:
        fh = open(CFG_NAME)
        cfg = json.loads(fh.read())
        fh.close()
        appin = machine.Pin(AP_PIN, machine.Pin.IN)
        if not appin.value():
            print("AP PIN request AP mode.")
            cfg['ap'] = 1
        cfg['message'] = "Config ready."
    except Exception as e:
        print("Error: %s" % e)
        print("No %s. Need AP mode." % CFG_NAME)
        cfg['ap'] = 1
    return cfg    

def connect(essid, pswd):
    global wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(essid, pswd)
    for i in range(CONNECT_WAIT):
        if wlan.isconnected():
            print('\nNetwork config:', wlan.ifconfig())
            try:
               ntptime.settime()
               print("Time synced")
            except Exception as e:
               print("NTP fail: " ,e )
            break
        print("%s ..." % (i), end='\r')
        time.sleep(1)

def start_ap():
    "Start WiFi Access POINT with default ESSID like ESP_XXXXXX and no password."
    global cfg, ap
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    while ap.active() == False:
        time.sleep(1)
    print("AP active: %s %s" % (ap.config('essid'), ap.ifconfig()))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', 80))
    s.listen(5)
    while True:
        try:
            conn, addr = s.accept()
            request = conn.recv(1024)
            print('Got a connection from %s'% str(addr))
            res = process_request(request)
            print("Res %s" % res)
            if not res:
                cfg = get_cfg()
                html = web_page(cfg)
            else:
                res['message'] = cfg['message']
                # check wifi connection
                wTest = network.WLAN(network.STA_IF)
                wTest.active(True)
                wTest.connect(res['essid'], res['pswd'])
                print('Connecting...')
                for i in range(CONNECT_WAIT):
                    time.sleep(1)
                    print("%s ..." % (i), end='\r')
                    if wTest.isconnected(): break
                if not wTest.isconnected():
                    res['message'] = "%s connection failed!" % res['essid']
                    res['ap'] = 1
                else:
                    res['message'] = "%s connected." % res['essid']
                    res['ap'] = 0
                    try:
                        fh = open(CFG_NAME, 'w')
                        fh.write(json.dumps(res))
                        fh.close()
                        res['message'] += ' Settings stored. Switch off AP and reboot.'
                    except Exception as e:
                        res['message'] += ' Settings storing error. %s' % e
                        print("Store cfg error: %s" % e)
                wTest.active(False)
                html = web_page(res)
                
            print('cfg: %s' % cfg)
            print(html)
            response = HTTP_RESPONSE % (len(html), html)
            conn.send(response)
            conn.close()
        except Exception as e:
                print("Req exception ", e)

def process_request(request):
    req = request.decode().split('\n')[0].strip().split(' ')
    print("Req decode: %s" % req)
    resdict = {}
    try:
        lst = req[1].split('?')[1].split('&')
        print('Lst: %s' % lst)
        fields = ('essid', 'pswd', 'tz')
        for part in lst:
            pair = part.split('=')
            resdict[pair[0]]=unquote(pair[1]).decode()
        if 'reboot' in resdict and resdict['reboot'] == 'on':
            print("REBOOT!")
            machine.reset()
    except Exception as e:
        print("Error processing request: %s" % e)
        resdict = None
    return resdict
    

def web_page(conf):
    global ap
    html = '<!doctype html><html><link rel="icon" href="data:;base64,Qk0eAAAAAAAAABoAAAAMAAAAAQABAAEAGAAAAP8A"><body><h1>%s</h1>' % ap.config('essid')
    html += """<form action="/" method="get"><p>ESSID:<input name="essid" type=text value="%(essid)s"></p>
<p>Password:<input name="pswd" type=text value="%(pswd)s"></p><p>Time: UTC + <input name="tz" type=text value="%(tz)s"></p>
<p><input type="submit" value="Submit">""" % conf
    print("Conf [ap]: %s" % conf['ap'])
    html+= '<input type="checkbox" name="reboot">Reboot' if not conf['ap'] else ''
    html+= "</p></form><hr>%(message)s</body></html>" % conf
    return html

def unquote(string):
    "unquote('abc%20de+f') -> b'abc de f'."
    global _hextobyte_cache

    # Note: strings are encoded as UTF-8. This is only an issue if it contains
    # unescaped non-ASCII characters, which URIs should not.
    if not string: return b''

    if isinstance(string, str):
        string = string.replace('+', ' ')
        string = string.encode('utf-8')

    bits = string.split(b'%')
    if len(bits) == 1: return string
            
    res = [bits[0]]
    append = res.append
        
    # Build cache for hex to char mapping on-the-fly only for codes
    # that are actually used
    if _hextobyte_cache is None: _hextobyte_cache = {}
    
    for item in bits[1:]:
        try:
            code = item[:2]
            char = _hextobyte_cache.get(code)
            if char is None: char = _hextobyte_cache[code] = bytes([int(code, 16)])
            append(char)
            append(item[2:])
        except KeyError:
            append(b'%')
            append(item)

    return b''.join(res)

_thread.start_new_thread(blink, ()) # bells and whistles

try:
    cfg=get_cfg()
    print(cfg)
    if 'ap' in cfg and cfg['ap']: start_ap()
    else: connect(cfg['essid'], cfg['pswd'])
except Exception as e:
    print("Except: %s" % e)    
    pass