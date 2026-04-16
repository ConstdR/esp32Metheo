#pragma once

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Wi-Fi credentials stored in NVS.
 */
typedef struct {
    char ssid[33];
    char password[65];
} wifi_creds_t;

/**
 * @brief Load Wi-Fi credentials from NVS.
 * @param creds  Pointer to result structure
 * @return true if valid credentials found
 */
bool ap_config_load(wifi_creds_t *creds);

/**
 * @brief Save Wi-Fi credentials to NVS.
 * @param creds  Pointer to credentials to save
 * @return true on success
 */
bool ap_config_save(const wifi_creds_t *creds);

/**
 * @brief Erase stored Wi-Fi credentials from NVS.
 * @return true on success
 */
bool ap_config_erase(void);

/**
 * @brief Set/clear the "last connection failed" NVS flag.
 *        When set, AP mode will show failure message with credentials.
 */
void ap_config_set_conn_failed(bool failed);

/**
 * @brief Check if the "last connection failed" flag is set.
 */
bool ap_config_get_conn_failed(void);

/**
 * @brief Start AP mode with captive portal for Wi-Fi configuration.
 *        Blocks forever until credentials are submitted (then reboots).
 * @param device_id    Device ID string for AP SSID suffix
 */
void ap_config_start(const char *device_id);
