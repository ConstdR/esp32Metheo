import machine
import ubinascii

MY_ID = str(ubinascii.hexlify(machine.unique_id()), 'utf-8')

AP_PIN = 34 # ground this pin to activate AP mode on reset
CFG_NAME = '_config' # store wifi ssid/pswd and tWeb ip:port
TS_NAME = '_timestamp' # timestamp for ntp
DATA_FILE = '_data' # measuring storage
DATA_LENGTH = 128 # number of records to store
CONNECT_WAIT = 10 # wait seconds to connect
LVL_PIN = 35 # 220k/220k to power/ground and middle node to check power level
LVL_LOWPWR = 1880 # low power level ~= 3.15V
LVL_SUNPIN = 33 # Solar battery level pin
LED_PIN = 5
DEEP_SLEEP = 900 # 900 == 15 min
I2CSCL_PIN = 22
I2CSDA_PIN = 21
I2C_FREQ = 10000

NTP_SYNC_PERIOD = DEEP_SLEEP*6 # ntp sync interval

FAKE_SLEEP = 0 # 1 -- no really go to deep sleep (for debug only)

POSITIONING = 'NOSCAN' # SCAN -- scan around, else -- calculated position

# positioning with mg995 servo
TZ = 1 # timezone difference with UTC in hours, could be float. NOT SUMMER TIME!
AZ_PIN = 25
ALT_PIN = 27

PWMMIN=20   # 18
PWMMAX=122  # 124

# Azimuth positioning ( could be specific by servo model/construction )
AZ_MIN=26   # 24   # +90°
AZ_MAX=126  # 128  # -90°
AZ_DUTYPERHOUR = (AZ_MAX-AZ_MIN)/12

# Alitude positioning ( could be specific by servo model/construction )
ALT_MIN=26  # 24   # Vertical   0°
ALT_MAX=72  # Horisontal 90°
ALT_LONGITUDE=50

