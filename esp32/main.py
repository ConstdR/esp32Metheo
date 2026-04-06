from espconf import *
from machine import SoftI2C, Pin, ADC, deepsleep
import mqttudp.engine as me
import time, json, BME280, sht30

def tstamp():
    return '%s-%.2d-%.2d %.2d:%.2d:%.2d' % time.localtime()[0:6]

def read_adc(pin):
    try:
        p = ADC(Pin(pin)); p.width(p.WIDTH_12BIT); p.atten(p.ATTN_11DB)
        return p.read_u16() / 10000
    except Exception as e: print(e); return 0

def measure():
    data = {'ts': tstamp(), 'v': read_adc(LVL_PIN), 'vs': read_adc(LVLSUN_PIN), 't': 0, 'h': 0, 'p': 0}
    try:
        sht = sht30.SHT30(I2C_SCL, I2C_SDA)
        if sht.is_present():
            data['t'], data['h'] = sht.measure()
        else:
            bme = BME280.BME280(i2c=SoftI2C(scl=Pin(I2C_SCL), sda=Pin(I2C_SDA)))
            data['t'], data['h'], data['p'] = bme.temperature, bme.humidity, bme.pressure
    except Exception as e:
        print(f"Measure error: {e}"); data['m'] = "No measure"
    return data

def run():
    while True:
        try:
            me.send_publish(f'weather/{MY_ID}/config', json.dumps({"sleep": DEEP_SLEEP, "ts_cfg": tstamp()}))
            me.send_publish(f'weather/{MY_ID}',        json.dumps(measure()))
            time.sleep(1)
        except Exception as e: print(f"Publish error: {e}")
        print(f"Deepsleep for {DEEP_SLEEP/1000}s")
        # deepsleep(DEEP_SLEEP)
        time.sleep(5)

run()
