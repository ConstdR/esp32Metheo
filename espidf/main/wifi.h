#pragma once

/**
 * @brief Initialize Wi-Fi in Station mode.
 *        Call once before wifi_wait_connected().
 */
void wifi_init_sta(const char *ssid, const char *password);

/**
 * @brief Block until IP address is obtained.
 */
void wifi_wait_connected(void);
