#pragma once
#include "sensor.h"
#include <stdint.h>

void mqttudp_client_init(void);

/**
 * Отправить данные сенсора как MQTT PUBLISH на топик "weather"
 * Payload: JSON {"id":"...","temperature":...,"humidity":...,"pressure":...,"ts":<unix_ms>}
 */
void mqttudp_send_sensor_data(const sensor_data_t *data,
                               const char *device_id,
                               int64_t unix_ms);
