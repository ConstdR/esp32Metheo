from machine import SoftI2C
import time

BME280_I2CADDR = 0x76

# Oversampling modes
BME280_OSAMPLE_1  = 1
BME280_OSAMPLE_2  = 2
BME280_OSAMPLE_4  = 3
BME280_OSAMPLE_8  = 4
BME280_OSAMPLE_16 = 5

# Registers
_R = type('R', (), {
    'DIG_T1': 0x88, 'DIG_T2': 0x8A, 'DIG_T3': 0x8C,
    'DIG_P1': 0x8E, 'DIG_P2': 0x90, 'DIG_P3': 0x92,
    'DIG_P4': 0x94, 'DIG_P5': 0x96, 'DIG_P6': 0x98,
    'DIG_P7': 0x9A, 'DIG_P8': 0x9C, 'DIG_P9': 0x9E,
    'DIG_H1': 0xA1, 'DIG_H2': 0xE1, 'DIG_H3': 0xE3,
    'DIG_H4': 0xE4, 'DIG_H5': 0xE5, 'DIG_H6': 0xE6, 'DIG_H7': 0xE7,
    'CONTROL_HUM': 0xF2, 'CONTROL': 0xF4,
    'PRESSURE': 0xF7, 'TEMP': 0xFA, 'HUMIDITY': 0xFD,
})()


class Device:
    """Low-level I2C register read/write helper."""
    def __init__(self, address, i2c):
        self._a, self._i = address, i2c

    def write8(self, reg, val):
        self._i.writeto_mem(self._a, reg, bytearray([val & 0xFF]))

    def readU8(self, reg):
        return int.from_bytes(self._i.readfrom_mem(self._a, reg, 1), 'little') & 0xFF

    def readS8(self, reg):
        r = self.readU8(reg); return r - 256 if r > 127 else r

    def readU16LE(self, reg):
        return int.from_bytes(self._i.readfrom_mem(self._a, reg, 2), 'little') & 0xFFFF

    def readS16LE(self, reg):
        r = self.readU16LE(reg); return r - 65536 if r > 32767 else r

    def read3(self, reg):
        """Read 3 bytes and return as 20-bit value (>> 4)."""
        d = self._i.readfrom_mem(self._a, reg, 3)
        return ((d[0] << 16) | (d[1] << 8) | d[2]) >> 4


class BME280:
    _MODES = (BME280_OSAMPLE_1, BME280_OSAMPLE_2, BME280_OSAMPLE_4,
              BME280_OSAMPLE_8, BME280_OSAMPLE_16)

    def __init__(self, mode=BME280_OSAMPLE_1, address=BME280_I2CADDR, i2c=None, **kwargs):
        if mode not in self._MODES:
            raise ValueError(f'Invalid mode {mode}')
        if i2c is None:
            raise ValueError('An I2C object is required.')
        self._mode   = mode
        self._device = Device(address, i2c)
        self.t_fine  = 0
        self._load_calibration()
        self._device.write8(_R.CONTROL, 0x3F)

    def _load_calibration(self):
        d = self._device
        (self.dig_T1, self.dig_T2, self.dig_T3) = (d.readU16LE(_R.DIG_T1), d.readS16LE(_R.DIG_T2), d.readS16LE(_R.DIG_T3))
        (self.dig_P1, self.dig_P2, self.dig_P3, self.dig_P4,
         self.dig_P5, self.dig_P6, self.dig_P7, self.dig_P8, self.dig_P9) = (
            d.readU16LE(_R.DIG_P1), d.readS16LE(_R.DIG_P2), d.readS16LE(_R.DIG_P3),
            d.readS16LE(_R.DIG_P4), d.readS16LE(_R.DIG_P5), d.readS16LE(_R.DIG_P6),
            d.readS16LE(_R.DIG_P7), d.readS16LE(_R.DIG_P8), d.readS16LE(_R.DIG_P9))
        self.dig_H1 = d.readU8(_R.DIG_H1)
        self.dig_H2 = d.readS16LE(_R.DIG_H2)
        self.dig_H3 = d.readU8(_R.DIG_H3)
        self.dig_H6 = d.readS8(_R.DIG_H7)
        h4 = (d.readS8(_R.DIG_H4) << 24) >> 20
        self.dig_H4 = h4 | (d.readU8(_R.DIG_H5) & 0x0F)
        h5 = (d.readS8(_R.DIG_H6) << 24) >> 20
        self.dig_H5 = h5 | (d.readU8(_R.DIG_H5) >> 4 & 0x0F)

    def read_raw_temp(self):
        m = self._mode
        self._device.write8(_R.CONTROL_HUM, m)
        self._device.write8(_R.CONTROL, m << 5 | m << 2 | 1)
        time.sleep_us(1250 + 2300 * (1 << m) * 3 + 575 * 2)
        return self._device.read3(_R.TEMP)

    def read_raw_pressure(self): return self._device.read3(_R.PRESSURE)

    def read_raw_humidity(self):
        d = self._device
        return (d.readU8(_R.HUMIDITY) << 8) | d.readU8(_R.HUMIDITY + 1)

    def read_temperature(self):
        adc  = self.read_raw_temp()
        var1 = ((adc >> 3) - (self.dig_T1 << 1)) * (self.dig_T2 >> 11)
        var2 = ((((adc >> 4) - self.dig_T1) ** 2) >> 12) * self.dig_T3 >> 14
        self.t_fine = var1 + var2
        return (self.t_fine * 5 + 128) >> 8

    def read_pressure(self):
        adc  = self.read_raw_pressure()
        var1 = self.t_fine - 128000
        var2 = var1 * var1 * self.dig_P6 + ((var1 * self.dig_P5) << 17) + (self.dig_P4 << 35)
        var1 = (((var1 * var1 * self.dig_P3) >> 8) + ((var1 * self.dig_P2) >> 12))
        var1 = (((1 << 47) + var1) * self.dig_P1) >> 33
        if var1 == 0: return 0
        p    = (((1048576 - adc) << 31) - var2) * 3125 // var1
        var1 = (self.dig_P9 * (p >> 13) * (p >> 13)) >> 25
        var2 = (self.dig_P8 * p) >> 19
        return ((p + var1 + var2) >> 8) + (self.dig_P7 << 4)

    def read_humidity(self):
        h = self.t_fine - 76800
        h = (((((self.read_raw_humidity() << 14) - (self.dig_H4 << 20) - (self.dig_H5 * h)) +
               16384) >> 15) *
             (((((((h * self.dig_H6) >> 10) * (((h * self.dig_H3) >> 11) + 32768)) >> 10) +
                2097152) * self.dig_H2 + 8192) >> 14))
        h -= (((h >> 15) ** 2 >> 7) * self.dig_H1) >> 4
        return max(0, min(h, 419430400)) >> 12

    @staticmethod
    def _fmt(val, divisor):
        i = val // divisor; return float(f"{i}.{val * 100 // divisor - i * 100:02d}")

    @property
    def temperature(self): return self._fmt(self.read_temperature(), 100)
    @property
    def pressure(self):    return self._fmt(self.read_pressure() // 256, 100)
    @property
    def humidity(self):    return self._fmt(self.read_humidity(), 1024)
