#pragma once
#include <stdbool.h>

/**
 * @brief Sensor data from BME280 + supply voltage
 */
typedef struct {
    float temperature;    ///< Temperature, °C
    float humidity;       ///< Humidity, %
    float pressure;       ///< Pressure, hPa
    float voltage;        ///< Supply voltage, V
    float voltage_solar;  ///< Solar panel voltage, V
} sensor_data_t;

/**
 * @brief Initialize I2C bus, BME280 and ADC.
 * @return true on success
 */
bool sensor_init(void);

/**
 * @brief Read data from BME280 and supply voltage.
 * @param out  Pointer to result structure
 * @return true on success
 */
bool sensor_read(sensor_data_t *out);
