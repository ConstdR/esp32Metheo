from machine import unique_id
from ubinascii import hexlify

MY_ID = str(hexlify(unique_id()), 'utf-8')

TZ=1

CFG_NAME='_config'

LED_PIN=5
M_PIN=25
AP_PIN=35
LVL_PIN = 34
LVLSUN_PIN = 33

CONNECT_WAIT=10
DEEP_SLEEP=30000 # 3s

# I2C
I2C_SCL=22
I2C_SDA=21