from machine import SoftI2C, Pin
import time

__version__ = '0.2.3.a'
__author__  = 'Roberto Snchez'
__license__ = "Apache License 2.0. https://www.apache.org/licenses/LICENSE-2.0"

DEFAULT_I2C_ADDRESS = 0x44

class SHT30Error(Exception):
    BUS_ERROR, DATA_ERROR, CRC_ERROR = 0x01, 0x02, 0x03
    _MSG = {0x01: "Bus error", 0x02: "Data error", 0x03: "CRC error"}
    def __init__(self, code=None):
        self.error_code = code
        super().__init__(self._MSG.get(code, "Unknown error"))

class SHT30:
    """SHT30 sensor driver in pure MicroPython over I2C."""
    POLYNOMIAL       = 0x131
    MEASURE_CMD      = b'\x2C\x10'
    STATUS_CMD       = b'\xF3\x2D'
    RESET_CMD        = b'\x30\xA2'
    CLEAR_STATUS_CMD = b'\x30\x41'
    ENABLE_HEATER_CMD  = b'\x30\x6D'
    DISABLE_HEATER_CMD = b'\x30\x66'

    # status register masks
    ALERT_PENDING_MASK = 0x8000
    HEATER_MASK        = 0x2000
    RH_ALERT_MASK      = 0x0800
    T_ALERT_MASK       = 0x0400
    RESET_MASK         = 0x0010
    CMD_STATUS_MASK    = 0x0002
    WRITE_STATUS_MASK  = 0x0001

    def __init__(self, scl_pin=5, sda_pin=4, delta_temp=0, delta_hum=0, i2c_address=DEFAULT_I2C_ADDRESS):
        self.i2c      = SoftI2C(scl=Pin(scl_pin), sda=Pin(sda_pin))
        self.i2c_addr = i2c_address
        self.set_delta(delta_temp, delta_hum)
        time.sleep_ms(50)

    def init(self, scl_pin=5, sda_pin=4):
        self.i2c.init(scl=Pin(scl_pin), sda=Pin(sda_pin))

    def is_present(self):
        return self.i2c_addr in self.i2c.scan()

    def set_delta(self, delta_temp=0, delta_hum=0):
        self.delta_temp = delta_temp
        self.delta_hum  = delta_hum

    def _check_crc(self, data):
        crc = 0xFF
        for b in data[:-1]:
            crc ^= b
            for _ in range(8):
                crc = (crc << 1) ^ SHT30.POLYNOMIAL if crc & 0x80 else crc << 1
        return data[-1] == crc

    def send_cmd(self, cmd, response_size=6, read_delay_ms=100):
        try:
            self.i2c.start()
            self.i2c.writeto(self.i2c_addr, cmd)
            if not response_size:
                self.i2c.stop(); return
            time.sleep_ms(read_delay_ms)
            data = self.i2c.readfrom(self.i2c_addr, response_size)
            self.i2c.stop()
            for i in range(response_size // 3):
                if not self._check_crc(data[i*3:(i+1)*3]):
                    raise SHT30Error(SHT30Error.CRC_ERROR)
            if data == bytearray(response_size):
                raise SHT30Error(SHT30Error.DATA_ERROR)
            return data
        except OSError as e:
            if 'I2C' in e.args[0]: raise SHT30Error(SHT30Error.BUS_ERROR)
            raise

    def clear_status(self): return self.send_cmd(SHT30.CLEAR_STATUS_CMD, None)
    def reset(self):         return self.send_cmd(SHT30.RESET_CMD, None)

    def status(self, raw=False):
        data = self.send_cmd(SHT30.STATUS_CMD, 3, read_delay_ms=20)
        return data if raw else (data[0] << 8 | data[1])

    def measure(self, raw=False):
        data = self.send_cmd(SHT30.MEASURE_CMD)
        if raw: return data
        t = (((data[0] << 8 | data[1]) * 175) / 0xFFFF) - 45 + self.delta_temp
        h = (((data[3] << 8 | data[4]) * 100.0) / 0xFFFF) + self.delta_hum
        return t, h

    def measure_int(self, raw=False):
        data = self.send_cmd(SHT30.MEASURE_CMD)
        if raw: return data
        ta = (data[0] << 8 | data[1]) * 175
        ha = (data[3] << 8 | data[4]) * 100
        return (ta // 0xFFFF) - 45, (ta % 0xFFFF * 100) // 0xFFFF, ha // 0xFFFF, (ha % 0xFFFF * 100) // 0xFFFF

