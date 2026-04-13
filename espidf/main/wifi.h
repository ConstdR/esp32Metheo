#pragma once

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Initialize Wi-Fi in Station mode.
 *        Call once before wifi_wait_connected().
 */
void wifi_init_sta(const char *ssid, const char *password);

/**
 * @brief Block until IP address is obtained.
 */
void wifi_wait_connected(void);

/**
 * @brief Block until IP address is obtained, with timeout.
 * @param timeout_ms  Maximum time to wait in milliseconds
 * @return true if connected, false on timeout
 */
bool wifi_wait_connected_timeout(uint32_t timeout_ms);

/**
 * @brief Stop Wi-Fi to save power before deep sleep.
 */
void wifi_stop(void);
