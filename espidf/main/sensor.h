#pragma once
#include <stdbool.h>

/**
 * @brief Данные с сенсора BME280 + напряжение питания
 */
typedef struct {
    float temperature;  ///< Температура, °C
    float humidity;     ///< Влажность, %
    float pressure;     ///< Давление, hPa
    float voltage;      ///< Напряжение питания, В
} sensor_data_t;

/**
 * @brief Инициализация I2C шины и BME280.
 * @return true если успешно
 */
bool sensor_init(void);

/**
 * @brief Считать данные с BME280 и напряжение питания.
 * @param out  Указатель на структуру для результата
 * @return true если успешно
 */
bool sensor_read(sensor_data_t *out);
