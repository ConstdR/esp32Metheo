#pragma once
#include "sensor.h"
#include <stdint.h>

/**
 * @brief Initialize UDP socket for MQTT-UDP broadcast.
 *        Call after wifi_wait_connected().
 */
void mqttudp_client_init(void);

/**
 * @brief Send sensor data as MQTT PUBLISH to topic "weather/<device_id>"
 *        Payload: JSON {"ts":"...","t":...,"h":...,"p":...,"v":...}
 */
void mqttudp_send_sensor_data(const sensor_data_t *data,
                               const char *device_id,
                               int64_t unix_ms);
