#pragma once

/**
 * @brief  Инициализация Wi-Fi в режиме Station.
 *         Вызывай один раз до wifi_wait_connected().
 */
void wifi_init_sta(const char *ssid, const char *password);

/**
 * @brief  Блокирует выполнение до получения IP-адреса.
 */
void wifi_wait_connected(void);
