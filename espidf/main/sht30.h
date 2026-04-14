#pragma once

#include "i2c_bus.h"
#include <stdbool.h>

#define SHT30_I2C_ADDRESS_DEFAULT  0x44
#define SHT30_I2C_ADDRESS_ALT     0x45

typedef struct {
    i2c_bus_handle_t bus;
    uint8_t addr;
} sht30_handle_t;

/**
 * @brief Check if SHT30 is present on the I2C bus.
 * @param bus   I2C bus handle (from i2c_bus_create)
 * @param addr  I2C address (0x44 or 0x45)
 * @return true if sensor responds
 */
bool sht30_is_present(i2c_bus_handle_t bus, uint8_t addr);

/**
 * @brief Read temperature and humidity from SHT30.
 * @param bus          I2C bus handle
 * @param addr         I2C address
 * @param temperature  Pointer to store temperature (°C)
 * @param humidity     Pointer to store relative humidity (%)
 * @return ESP_OK on success
 */
esp_err_t sht30_read(i2c_bus_handle_t bus, uint8_t addr,
                     float *temperature, float *humidity);
