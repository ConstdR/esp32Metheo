#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_mac.h"
#include "esp_rtc_time.h"
#include "esp_sntp.h"
#include "driver/gpio.h"
#include <time.h>

#include "wifi.h"
#include "sensor.h"
#include "mqttudp_client.h"

static const char *TAG = "main";

/* ── Настройки из menuconfig (idf.py menuconfig) ────────────── */
#define WIFI_SSID        CONFIG_WIFI_SSID
#define WIFI_PASSWORD    CONFIG_WIFI_PASSWORD
#define LED_GPIO         CONFIG_LED_GPIO
#define SLEEP_MINUTES    CONFIG_SLEEP_MINUTES
#define SLEEP_US         (SLEEP_MINUTES * 60ULL * 1000000ULL)

/* NTP синхронизация раз в сутки */
#define NTP_SERVER       "pool.ntp.org"
#define NTP_SYNC_SECS    (24 * 60 * 60)  // 24 часа в секундах

/* ── RTC memory — сохраняется при deep sleep ─────────────────── */
RTC_DATA_ATTR static int64_t  s_saved_unix_ms = 0;   // Unix время в мс на момент синхронизации
RTC_DATA_ATTR static uint64_t s_saved_rtc_us  = 0;   // RTC счётчик на момент синхронизации
RTC_DATA_ATTR static int64_t  s_last_sync_unix = 0;  // Unix время последней NTP синхронизации

/* ── Текущее Unix время в мс ─────────────────────────────────── */
static int64_t get_unix_ms(void)
{
    if (s_saved_unix_ms == 0) {
        return 0;  // не синхронизировано
    }
    uint64_t rtc_now = esp_rtc_get_time_us();
    int64_t elapsed_ms = (int64_t)((rtc_now - s_saved_rtc_us) / 1000ULL);
    return s_saved_unix_ms + elapsed_ms;
}

/* ── NTP синхронизация ───────────────────────────────────────── */
static EventGroupHandle_t s_ntp_event_group;
#define NTP_SYNCED_BIT BIT0

static void ntp_sync_callback(struct timeval *tv)
{
    xEventGroupSetBits(s_ntp_event_group, NTP_SYNCED_BIT);
}

static bool ntp_sync(void)
{
    ESP_LOGI(TAG, "NTP sync from %s...", NTP_SERVER);

    s_ntp_event_group = xEventGroupCreate();

    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, NTP_SERVER);
    sntp_set_time_sync_notification_cb(ntp_sync_callback);
    esp_sntp_init();

    /* Ждём синхронизации до 30 секунд */
    EventBits_t bits = xEventGroupWaitBits(s_ntp_event_group, NTP_SYNCED_BIT,
                                           pdFALSE, pdTRUE,
                                           pdMS_TO_TICKS(30000));
    esp_sntp_stop();
    vEventGroupDelete(s_ntp_event_group);

    if (!(bits & NTP_SYNCED_BIT)) {
        ESP_LOGW(TAG, "NTP sync timeout");
        return false;
    }

    /* Сохраняем в RTC memory */
    struct timeval tv;
    gettimeofday(&tv, NULL);
    s_saved_unix_ms  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
    s_saved_rtc_us   = esp_rtc_get_time_us();
    s_last_sync_unix = (int64_t)tv.tv_sec;

    ESP_LOGI(TAG, "NTP synced: unix=%lld", (long long)tv.tv_sec);
    return true;
}

/* ── Нужна ли NTP синхронизация? ────────────────────────────── */
static bool need_ntp_sync(void)
{
    if (s_saved_unix_ms == 0) return true;  // первый boot

    int64_t now_unix = get_unix_ms() / 1000LL;
    return (now_unix - s_last_sync_unix) >= NTP_SYNC_SECS;
}

/* ── Device ID из MAC адреса ─────────────────────────────────── */
static void get_device_id(char *buf, size_t buf_size)
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(buf, buf_size,
             "%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

/* ── Точка входа ─────────────────────────────────────────────── */
void app_main(void)
{
    uint32_t causes = esp_sleep_get_wakeup_causes();
    if (causes & BIT(ESP_SLEEP_WAKEUP_TIMER)) {
        ESP_LOGI(TAG, "Wakeup from deep sleep");
    } else {
        ESP_LOGI(TAG, "First boot");
    }

    /* Device ID */
    char device_id[13];
    get_device_id(device_id, sizeof(device_id));
    ESP_LOGI(TAG, "Device ID: %s", device_id);

    /* 0. LED */
    gpio_config_t led_conf = {
        .pin_bit_mask = (1ULL << LED_GPIO),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&led_conf);
    gpio_set_level(LED_GPIO, 0);

    /* 1. Wi-Fi */
    wifi_init_sta(WIFI_SSID, WIFI_PASSWORD);
    wifi_wait_connected();

    /* 2. NTP — только если нужно (первый boot или прошло 24 часа) */
    if (need_ntp_sync()) {
        ntp_sync();
    } else {
        ESP_LOGI(TAG, "NTP skip, last sync %lld sec ago",
                 (long long)(get_unix_ms() / 1000LL - s_last_sync_unix));
    }

    /* 3. Сенсор BME280 */
    esp_log_level_set("i2c.master", ESP_LOG_NONE);
    if (!sensor_init()) {
        ESP_LOGE(TAG, "BME280 init failed! Going to sleep anyway.");
        goto deep_sleep;
    }
    esp_log_level_set("i2c.master", ESP_LOG_ERROR);

    vTaskDelay(pdMS_TO_TICKS(500));

    /* 4. MQTT-UDP */
    mqttudp_client_init();

    /* 5. Измерение и отправка */
    {
        sensor_data_t data;
        gpio_set_level(LED_GPIO, 1);
        if (sensor_read(&data)) {
            mqttudp_send_sensor_data(&data, device_id, get_unix_ms());
        } else {
            ESP_LOGW(TAG, "Sensor read failed");
        }
        gpio_set_level(LED_GPIO, 0);
    }

    vTaskDelay(pdMS_TO_TICKS(200));

deep_sleep:
    ESP_LOGI(TAG, "Going to deep sleep for %d min...", SLEEP_MINUTES);
    esp_sleep_enable_timer_wakeup(SLEEP_US);
    esp_deep_sleep_start();
}
