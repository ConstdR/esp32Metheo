idf_component_register(
    SRCS
        "main.c"
        "wifi.c"
        "sensor.c"
        "mqttudp_client.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_wifi
        nvs_flash
        driver
        lwip
        esp_netif
        esp_event
        esp_driver_gpio
        esp_hw_support
        espressif__bme280
)
