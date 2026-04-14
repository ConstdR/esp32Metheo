#include "sensor.h"

#include "bme280.h"
#include "i2c_bus.h"
#include "sht30.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"

static const char *TAG = "sensor";

/* ── I2C pins ────────────────────────────────────────────────── */
#define I2C_MASTER_SCL_IO    CONFIG_I2C_SCL_GPIO
#define I2C_MASTER_SDA_IO    CONFIG_I2C_SDA_GPIO
#define I2C_MASTER_FREQ_HZ   100000
#define I2C_PORT             I2C_NUM_0

/*
 * BME280 address:
 *   SDO → GND   →  0x76  (BME280_I2C_ADDRESS_DEFAULT)
 *   SDO → 3.3V  →  0x77
 */
#define BME280_ADDR  BME280_I2C_ADDRESS_DEFAULT

/* ── GPIO to ADC1 channel mapping (ESP32) ───────────────────── */
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

/* ── ADC for supply voltage (GPIO configurable via menuconfig) ── */
#define VOLTAGE_DIVIDER      2.0f   // 110k/110k divider = 1:2
#define VOLTAGE_ADC_CHANNEL  gpio_to_adc1_channel(CONFIG_BATTERY_ADC_GPIO)

#if CONFIG_SOLAR_ENABLED
/* ── ADC for solar panel voltage (optional) ─────────────────── */
#define SOLAR_DIVIDER        2.0f   // 110k/110k divider = 1:2
#define SOLAR_ADC_CHANNEL    gpio_to_adc1_channel(CONFIG_SOLAR_ADC_GPIO)
#endif

/* ── Sensor type ────────────────────────────────────────────── */
typedef enum {
    SENSOR_NONE,
    SENSOR_BME280,
    SENSOR_SHT30,
} sensor_type_t;

static i2c_bus_handle_t          i2c_bus    = NULL;
static bme280_handle_t           bme280     = NULL;
static adc_oneshot_unit_handle_t adc_handle = NULL;
static adc_cali_handle_t         adc_cali   = NULL;
static sensor_type_t             active_sensor = SENSOR_NONE;

/* ── Try to init BME280 ─────────────────────────────────────── */
static bool init_bme280(void)
{
    bme280 = bme280_create(i2c_bus, BME280_ADDR);
    if (bme280 == NULL) {
        ESP_LOGD(TAG, "bme280_create failed (addr=0x%02X)", BME280_ADDR);
        return false;
    }
    if (bme280_default_init(bme280) != ESP_OK) {
        ESP_LOGD(TAG, "bme280_default_init failed");
        return false;
    }
    ESP_LOGI(TAG, "BME280 initialized (addr=0x%02X)", BME280_ADDR);
    return true;
}

/* ── Try to init SHT30 ──────────────────────────────────────── */
static bool init_sht30(void)
{
    if (!sht30_is_present(i2c_bus, SHT30_I2C_ADDRESS_DEFAULT)) {
        ESP_LOGI(TAG, "SHT30 not found at 0x%02X", SHT30_I2C_ADDRESS_DEFAULT);
        return false;
    }
    ESP_LOGI(TAG, "SHT30 initialized (addr=0x%02X)", SHT30_I2C_ADDRESS_DEFAULT);
    return true;
}

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

    /* Detect / init sensor based on menuconfig choice */
#if CONFIG_SENSOR_AUTO
    /* Auto-detect: try SHT30 first, then BME280 */
    if (init_sht30()) {
        active_sensor = SENSOR_SHT30;
    } else if (init_bme280()) {
        active_sensor = SENSOR_BME280;
    }
#elif CONFIG_SENSOR_SHT30
    if (init_sht30()) {
        active_sensor = SENSOR_SHT30;
    }
#else  /* CONFIG_SENSOR_BME280 */
    if (init_bme280()) {
        active_sensor = SENSOR_BME280;
    }
#endif

    if (active_sensor == SENSOR_NONE) {
        ESP_LOGE(TAG, "No sensor found! (SDA=%d, SCL=%d)", I2C_MASTER_SDA_IO, I2C_MASTER_SCL_IO);
        return false;
    }

    /* Initialize ADC unit */
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

#if CONFIG_SOLAR_ENABLED
    adc_oneshot_config_channel(adc_handle, SOLAR_ADC_CHANNEL, &chan_cfg);
#endif

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
    /* Read temperature / humidity / pressure from active sensor */
    if (active_sensor == SENSOR_SHT30) {
        /* SHT30: temperature + humidity only, no pressure */
        esp_err_t err = sht30_read(i2c_bus, SHT30_I2C_ADDRESS_DEFAULT,
                                   &out->temperature, &out->humidity);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "SHT30 read failed");
            return false;
        }
        out->pressure = 0.0f;  /* SHT30 has no pressure sensor */
        ESP_LOGI(TAG, "[SHT30] T: %.1f°C  H: %.1f%%",
                 out->temperature, out->humidity);

    } else {
        /* BME280: temperature + humidity + pressure */
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
        ESP_LOGI(TAG, "[BME280] T: %.1f°C  H: %.1f%%  P: %.1f hPa",
                 out->temperature, out->humidity, out->pressure);
    }

    /* Read supply voltage via divider */
    int raw = 0, mv = 0;
    adc_oneshot_read(adc_handle, VOLTAGE_ADC_CHANNEL, &raw);
    if (adc_cali) {
        adc_cali_raw_to_voltage(adc_cali, raw, &mv);
    } else {
        mv = raw * 3300 / 4095;
    }
    out->voltage = (mv / 1000.0f) * VOLTAGE_DIVIDER;
    ESP_LOGI(TAG, "V: %.2fV (GPIO%d raw=%d)", out->voltage, CONFIG_BATTERY_ADC_GPIO, raw);

#if CONFIG_SOLAR_ENABLED
    /* Read solar panel voltage via divider */
    adc_oneshot_read(adc_handle, SOLAR_ADC_CHANNEL, &raw);
    mv = 0;
    if (adc_cali) {
        adc_cali_raw_to_voltage(adc_cali, raw, &mv);
    } else {
        mv = raw * 3300 / 4095;
    }
    out->voltage_solar = (mv / 1000.0f) * SOLAR_DIVIDER;
    ESP_LOGI(TAG, "Vs: %.2fV (GPIO%d raw=%d)", out->voltage_solar, CONFIG_SOLAR_ADC_GPIO, raw);
#else
    out->voltage_solar = 0.0f;
#endif

    return true;
}
