#include "sensor.h"

#include "bme280.h"
#include "i2c_bus.h"
#include "esp_log.h"

static const char *TAG = "sensor";

/* ── Пины I2C ─────────────────────────────────────────────────── */
#define I2C_MASTER_SCL_IO    22
#define I2C_MASTER_SDA_IO    21
#define I2C_MASTER_FREQ_HZ   100000
#define I2C_PORT             I2C_NUM_0

/*
 * Адрес BME280:
 *   SDO → GND   →  0x76  (BME280_I2C_ADDRESS_DEFAULT)
 *   SDO → 3.3V  →  0x77
 */
#define BME280_ADDR  BME280_I2C_ADDRESS_DEFAULT

static i2c_bus_handle_t  i2c_bus = NULL;
static bme280_handle_t   bme280  = NULL;

/* ── Публичные функции ───────────────────────────────────────── */
bool sensor_init(void)
{
    /* Инициализация i2c_bus */
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

    /* Создание handle BME280 */
    bme280 = bme280_create(i2c_bus, BME280_ADDR);
    if (bme280 == NULL) {
        ESP_LOGE(TAG, "bme280_create failed (check wiring, addr=0x%02X)", BME280_ADDR);
        return false;
    }

    /* Инициализация с настройками по умолчанию */
    if (bme280_default_init(bme280) != ESP_OK) {
        ESP_LOGE(TAG, "bme280_default_init failed");
        return false;
    }

    ESP_LOGI(TAG, "BME280 initialized (addr=0x%02X, SDA=%d, SCL=%d)",
             BME280_ADDR, I2C_MASTER_SDA_IO, I2C_MASTER_SCL_IO);
    return true;
}

bool sensor_read(sensor_data_t *out)
{
    /* BME280 в normal mode измеряет непрерывно — просто читаем данные */
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
    return true;
}
