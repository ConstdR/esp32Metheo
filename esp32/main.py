from espconf import *
from machine import SoftI2C, deepsleep
import mqttudp.engine as me
import time
import BME280
import sht30

def run():
    wdt = machine.WDT(timeout=DEEP_SLEEP*2)
    while True:
        tstump = '%s-%.2d-%.2d %.2d:%.2d:%.2d' % time.localtime()[0:6]
        config = { "sleep": DEEP_SLEEP, "ts_cfg": tstump}
        try:
            print("Publish config: %s %s" % (MY_ID, config))
            me.send_publish('weather/%s/config' % MY_ID, json.dumps(config))
            data = measure()
            print("Publish data: %s " % data)
            me.send_publish('weather/%s' % MY_ID, json.dumps(data))
            time.sleep(1) # ? udp send async ?
        except Exception as e:
            print("Publish exception: %s" % e)
        print("Deepsleep for %s sec" % str(DEEP_SLEEP/1000))
        deepsleep(DEEP_SLEEP)
        # time.sleep(10)
        
def measure():
    tstump = '%s-%.2d-%.2d %.2d:%.2d:%.2d' % time.localtime()[0:6]
    data = {'ts': tstump, 'v':read_adc(LVL_PIN), 'vs':read_adc(LVLSUN_PIN), 't':0, 'h':0, 'p':0}
    try:
        sht = sht30.SHT30(I2C_SCL, I2C_SDA)
        if sht.is_present():
            (data['t'], data['h']) = sht.measure()
        else:
            try:
                i2c = SoftI2C(scl=machine.Pin(I2C_SCL), sda=machine.Pin(I2C_SDA))
                bme = BME280.BME280(i2c=i2c)
                (data['t'], data['h'], data['p']) = (bme.temperature, bme.humidity, bme.pressure)
            except Exception as e:
                data['m'] = "No measure"
                print (data['m'])
    except Exception as e:
        print("Measure Exception")
        print(e)
    return data

def read_adc(pin):
    res=0
    try:
        p=machine.ADC(machine.Pin(pin))
        p.width(p.WIDTH_12BIT)
        p.atten(p.ATTN_11DB)
        res=p.read_u16()/10000
    except Exception as e:
        print(e)
    return res
        
run()
