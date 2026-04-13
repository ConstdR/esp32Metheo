#include "sensor.h"

#include "bme280.h"
#include "i2c_bus.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"

static const char *TAG = "sensor";

/* ── I2C pins ────────────────────────────────────────────────── */
#define I2C_MASTER_SCL_IO    22
#define I2C_MASTER_SDA_IO    21
#define I2C_MASTER_FREQ_HZ   100000
#define I2C_PORT             I2C_NUM_0

/*
 * BME280 address:
 *   SDO → GND   →  0x76  (BME280_I2C_ADDRESS_DEFAULT)
 *   SDO → 3.3V  →  0x77
 */
#define BME280_ADDR  BME280_I2C_ADDRESS_DEFAULT

/* ── ADC for supply voltage measurement ──────────────────────── */
#define VOLTAGE_DIVIDER      2.0f            // 110k/110k divider = 1:2

/* ── ADC for solar panel voltage measurement ─────────────────── */
#define SOLAR_DIVIDER        2.0f            // 110k/110k divider = 1:2

/* ── GPIO to ADC1 channel mapping (ESP32) ──────────────────── */
static adc_channel_t gpio_to_adc1_channel(int gpio)
{
    switch (gpio) {
        case 32: return ADC_CHANNEL_4;
        case 33: return ADC_CHANNEL_5;
        case 34: return ADC_CHANNEL_6;
        case 35: return ADC_CHANNEL_7;
        case 36: return ADC_CHANNEL_0;
        case 37: return ADC_CHANNEL_1;
        case 38: return ADC_CHANNEL_2;
        case 39: return ADC_CHANNEL_3;
        default: return ADC_CHANNEL_6;  // fallback
    }
}

#define VOLTAGE_ADC_CHANNEL  gpio_to_adc1_channel(CONFIG_BATTERY_ADC_GPIO)
#define SOLAR_ADC_CHANNEL    gpio_to_adc1_channel(CONFIG_SOLAR_ADC_GPIO)

static i2c_bus_handle_t          i2c_bus      = NULL;
static bme280_handle_t           bme280       = NULL;
static adc_oneshot_unit_handle_t adc_handle   = NULL;
static adc_cali_handle_t         adc_cali     = NULL;

/* ── Public functions ────────────────────────────────────────── */
bool sensor_init(void)
{
    /* Initialize I2C bus */
    i2c_config_t conf = {
        .mode             = I2C_MODE_MASTER,
        .sda_io_num       = I2C_MASTER_SDA_IO,
        .scl_io_num       = I2C_MASTER_SCL_IO,
        .sda_pullup_en    = GPIO_PULLUP_ENABLE,
        .scl_pullup_en    = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
    };
    i2c_bus = i2c_bus_create(I2C_PORT, &conf);
    if (i2c_bus == NULL) {
        ESP_LOGE(TAG, "i2c_bus_create failed");
        return false;
    }

    /* Create BME280 handle */
    bme280 = bme280_create(i2c_bus, BME280_ADDR);
    if (bme280 == NULL) {
        ESP_LOGE(TAG, "bme280_create failed (check wiring, addr=0x%02X)", BME280_ADDR);
        return false;
    }

    /* Initialize with default settings */
    if (bme280_default_init(bme280) != ESP_OK) {
        ESP_LOGE(TAG, "bme280_default_init failed");
        return false;
    }

    ESP_LOGI(TAG, "BME280 initialized (addr=0x%02X, SDA=%d, SCL=%d)",
             BME280_ADDR, I2C_MASTER_SDA_IO, I2C_MASTER_SCL_IO);

    /* Initialize ADC for voltage measurement */
    adc_oneshot_unit_init_cfg_t adc_cfg = {
        .unit_id  = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    adc_oneshot_new_unit(&adc_cfg, &adc_handle);

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten    = ADC_ATTEN_DB_12,   // 0-3.3V range
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    adc_oneshot_config_channel(adc_handle, VOLTAGE_ADC_CHANNEL, &chan_cfg);
    adc_oneshot_config_channel(adc_handle, SOLAR_ADC_CHANNEL,   &chan_cfg);

    /* ADC calibration */
    adc_cali_line_fitting_config_t cali_cfg = {
        .unit_id  = ADC_UNIT_1,
        .atten    = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    esp_err_t cali_ret = adc_cali_create_scheme_line_fitting(&cali_cfg, &adc_cali);
    if (cali_ret != ESP_OK) {
        ESP_LOGW(TAG, "ADC calibration not available, using raw values");
        adc_cali = NULL;
    }

    return true;
}

bool sensor_read(sensor_data_t *out)
{
    /* BME280 runs in normal mode — data is ready after init + short delay.
     * Forced mode would save ~3.6µA but requires raw register writes
     * since espressif/bme280 component doesn't expose mode control.
     * Not worth the complexity given deep sleep kills power anyway. */
    if (bme280_read_temperature(bme280, &out->temperature) != ESP_OK) {
        ESP_LOGE(TAG, "Read temperature failed");
        return false;
    }

    if (bme280_read_humidity(bme280, &out->humidity) != ESP_OK) {
        ESP_LOGE(TAG, "Read humidity failed");
        return false;
    }

    if (bme280_read_pressure(bme280, &out->pressure) != ESP_OK) {
        ESP_LOGE(TAG, "Read pressure failed");
        return false;
    }

    ESP_LOGI(TAG, "T: %.1f°C  H: %.1f%%  P: %.1f hPa",
             out->temperature, out->humidity, out->pressure);

    /* Read supply voltage via voltage divider on GPIO %d */
    int raw = 0;
    adc_oneshot_read(adc_handle, VOLTAGE_ADC_CHANNEL, &raw);

    int voltage_mv = 0;
    if (adc_cali) {
        adc_cali_raw_to_voltage(adc_cali, raw, &voltage_mv);
    } else {
        voltage_mv = raw * 3300 / 4095;  // fallback without calibration
    }
    out->voltage = (voltage_mv / 1000.0f) * VOLTAGE_DIVIDER;
    ESP_LOGI(TAG, "V: %.2f V (GPIO%d, raw=%d, mv=%d)",
             out->voltage, CONFIG_BATTERY_ADC_GPIO, raw, voltage_mv);

    /* Read solar panel voltage via voltage divider on GPIO %d */
    adc_oneshot_read(adc_handle, SOLAR_ADC_CHANNEL, &raw);
    int solar_mv = 0;
    if (adc_cali) {
        adc_cali_raw_to_voltage(adc_cali, raw, &solar_mv);
    } else {
        solar_mv = raw * 3300 / 4095;
    }
    out->voltage_solar = (solar_mv / 1000.0f) * SOLAR_DIVIDER;
    ESP_LOGI(TAG, "Vs: %.2f V (GPIO%d, raw=%d, mv=%d)",
             out->voltage_solar, CONFIG_SOLAR_ADC_GPIO, raw, solar_mv);

    return true;
}
