#include "sht30.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "sht30";

/*
 * SHT30 I2C protocol: 2-byte commands, 6-byte measurement response.
 *
 * We use the i2c_bus API where the first byte of a command is passed
 * as mem_address and the rest as data — on the wire this is identical
 * to sending two raw bytes.
 *
 * i2c_bus_write_bytes(dev, cmd_hi, 1, &cmd_lo)  →  [START][ADDR+W][cmd_hi][cmd_lo][STOP]
 * i2c_bus_read_bytes(dev, cmd_hi, N, buf)        →  [START][ADDR+W][cmd_hi][Sr][ADDR+R][N bytes][STOP]
 */

#define SHT30_RESPONSE_LEN   6

/* CRC-8 polynomial: x^8 + x^5 + x^4 + 1 = 0x131 */
static uint8_t sht30_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 0x80) {
                crc = (crc << 1) ^ 0x31;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

/* ── Create a temporary i2c_bus_device for given address ─────── */
static i2c_bus_device_handle_t sht30_dev_create(i2c_bus_handle_t bus, uint8_t addr)
{
    return i2c_bus_device_create(bus, addr, 0);
}

bool sht30_is_present(i2c_bus_handle_t bus, uint8_t addr)
{
    i2c_bus_device_handle_t dev = sht30_dev_create(bus, addr);
    if (dev == NULL) return false;

    /* Read status register: command 0xF3 0x2D
     * Send 0xF3 as mem_address, 0x2D as one byte of data */
    uint8_t cmd_lo = 0x2D;
    esp_err_t err = i2c_bus_write_bytes(dev, 0xF3, 1, &cmd_lo);
    if (err != ESP_OK) {
        i2c_bus_device_delete(&dev);
        return false;
    }

    vTaskDelay(pdMS_TO_TICKS(20));

    /* Read 3 bytes: status_hi, status_lo, crc */
    uint8_t resp[3] = {0};
    err = i2c_bus_read_bytes(dev, 0xF3, sizeof(resp), resp);
    i2c_bus_device_delete(&dev);

    if (err != ESP_OK) return false;
    if (sht30_crc8(resp, 2) != resp[2]) return false;

    ESP_LOGI(TAG, "SHT30 found at 0x%02X (status=0x%02X%02X)", addr, resp[0], resp[1]);
    return true;
}

esp_err_t sht30_read(i2c_bus_handle_t bus, uint8_t addr,
                     float *temperature, float *humidity)
{
    i2c_bus_device_handle_t dev = sht30_dev_create(bus, addr);
    if (dev == NULL) return ESP_ERR_NO_MEM;

    /* Send measurement command: 0x2C 0x06 (high repeatability, clock stretch)
     * 0x2C as mem_address, 0x06 as data byte */
    uint8_t cmd_lo = 0x06;
    esp_err_t err = i2c_bus_write_bytes(dev, 0x2C, 1, &cmd_lo);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Write measure command failed: %s", esp_err_to_name(err));
        i2c_bus_device_delete(&dev);
        return err;
    }

    /* Wait for measurement (max 15ms for high repeatability) */
    vTaskDelay(pdMS_TO_TICKS(20));

    /* Read 6 bytes: T_MSB, T_LSB, T_CRC, H_MSB, H_LSB, H_CRC
     * Use 0x00 as dummy mem_address — SHT30 ignores it on read */
    uint8_t data[SHT30_RESPONSE_LEN] = {0};
    err = i2c_bus_read_bytes(dev, 0x00, SHT30_RESPONSE_LEN, data);
    i2c_bus_device_delete(&dev);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Read data failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Verify CRC for temperature */
    if (sht30_crc8(data, 2) != data[2]) {
        ESP_LOGE(TAG, "Temperature CRC mismatch");
        return ESP_ERR_INVALID_CRC;
    }

    /* Verify CRC for humidity */
    if (sht30_crc8(data + 3, 2) != data[5]) {
        ESP_LOGE(TAG, "Humidity CRC mismatch");
        return ESP_ERR_INVALID_CRC;
    }

    /* Convert raw values */
    uint16_t raw_t = (data[0] << 8) | data[1];
    uint16_t raw_h = (data[3] << 8) | data[4];

    *temperature = -45.0f + 175.0f * ((float)raw_t / 65535.0f);
    *humidity    = 100.0f * ((float)raw_h / 65535.0f);

    return ESP_OK;
}
