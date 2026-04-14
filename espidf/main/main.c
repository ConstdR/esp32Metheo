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
#include "ap_config.h"

static const char *TAG = "main";

/* ── Settings from menuconfig (idf.py menuconfig) ───────────── */
#define LED_GPIO         CONFIG_LED_GPIO
#if CONFIG_LED_INVERTED
#define LED_ON  0
#define LED_OFF 1
#else
#define LED_ON  1
#define LED_OFF 0
#endif
#define SLEEP_MINUTES    CONFIG_SLEEP_MINUTES
#define SLEEP_US         (SLEEP_MINUTES * 60ULL * 1000000ULL)

/* AP configuration mode — from menuconfig */
#define AP_TIMEOUT_SEC       CONFIG_AP_TIMEOUT_SEC
#define WIFI_FAIL_THRESHOLD  CONFIG_WIFI_FAIL_THRESHOLD

/* NTP sync once per day */
#define NTP_SERVER       "pool.ntp.org"
#define NTP_SYNC_SECS    (24 * 60 * 60)

/* ── RTC memory — preserved during deep sleep ────────────────── */
RTC_DATA_ATTR static int64_t  s_saved_unix_ms   = 0;  // Unix time in ms at sync moment
RTC_DATA_ATTR static uint64_t s_saved_rtc_us    = 0;  // RTC counter at sync moment
RTC_DATA_ATTR static int64_t  s_last_sync_unix  = 0;  // Unix time of last NTP sync
RTC_DATA_ATTR static uint32_t s_wifi_fail_count = 0;   // consecutive Wi-Fi failures

/* ── Get current Unix time in ms ────────────────────────────── */
static int64_t get_unix_ms(void)
{
    if (s_saved_unix_ms == 0) {
        return 0;  // not synced yet
    }
    uint64_t rtc_now = esp_rtc_get_time_us();
    int64_t elapsed_ms = (int64_t)((rtc_now - s_saved_rtc_us) / 1000ULL);
    return s_saved_unix_ms + elapsed_ms;
}

/* ── NTP sync ────────────────────────────────────────────────── */
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

    /* Wait up to 30 seconds for sync */
    EventBits_t bits = xEventGroupWaitBits(s_ntp_event_group, NTP_SYNCED_BIT,
                                           pdFALSE, pdTRUE,
                                           pdMS_TO_TICKS(30000));
    esp_sntp_stop();
    vEventGroupDelete(s_ntp_event_group);

    if (!(bits & NTP_SYNCED_BIT)) {
        ESP_LOGW(TAG, "NTP sync timeout");
        return false;
    }

    /* Save to RTC memory */
    struct timeval tv;
    gettimeofday(&tv, NULL);
    s_saved_unix_ms  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
    s_saved_rtc_us   = esp_rtc_get_time_us();
    s_last_sync_unix = (int64_t)tv.tv_sec;

    ESP_LOGI(TAG, "NTP synced: unix=%lld", (long long)tv.tv_sec);
    return true;
}

/* ── Check if NTP sync is needed ────────────────────────────── */
static bool need_ntp_sync(void)
{
    if (s_saved_unix_ms == 0) return true;  // first boot

    int64_t now_unix = get_unix_ms() / 1000LL;
    return (now_unix - s_last_sync_unix) >= NTP_SYNC_SECS;
}

/* ── Device ID from MAC address ─────────────────────────────── */
static void get_device_id(char *buf, size_t buf_size)
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(buf, buf_size,
             "%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

/* ── Entry point ─────────────────────────────────────────────── */
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
    gpio_set_level(LED_GPIO, LED_OFF);

    /* 1. Common Wi-Fi/NVS init (needed for both STA and AP modes) */
    wifi_common_init();

    /* 2. Load Wi-Fi credentials from NVS */
    wifi_creds_t creds;
    if (!ap_config_load(&creds)) {
        ESP_LOGW(TAG, "No Wi-Fi credentials in NVS → AP configuration mode");
        ap_config_start(AP_TIMEOUT_SEC, device_id);
        /* If AP times out without config, go to deep sleep */
        goto deep_sleep;
    }

    /* 3. Check consecutive Wi-Fi failure count */
    if (s_wifi_fail_count >= WIFI_FAIL_THRESHOLD) {
        ESP_LOGW(TAG, "Wi-Fi failed %lu times in a row → AP configuration mode",
                 (unsigned long)s_wifi_fail_count);
        /* Don't erase old credentials — just offer a chance to reconfigure.
         * If nobody reconfigures (AP times out), reset counter so that
         * on next wake we try the old SSID again. */
        ap_config_start(AP_TIMEOUT_SEC, device_id);
        s_wifi_fail_count = 0;
        goto deep_sleep;
    }

    /* 4. Start Wi-Fi STA (non-blocking) */
    wifi_init_sta(creds.ssid, creds.password);

    /* 5. While Wi-Fi connects, init BME280 in parallel */
    bool sensor_ok = false;
    esp_log_level_set("i2c.master", ESP_LOG_NONE);
    sensor_ok = sensor_init();
    esp_log_level_set("i2c.master", ESP_LOG_ERROR);
    if (!sensor_ok) {
        ESP_LOGE(TAG, "BME280 init failed! Going to sleep anyway.");
    }

    /* 6. Wait for Wi-Fi with timeout */
    if (!wifi_wait_connected_timeout(15000)) {
        s_wifi_fail_count++;
        ap_config_set_conn_failed(true);
        ESP_LOGE(TAG, "Wi-Fi failed (attempt %lu/%d), going to sleep",
                 (unsigned long)s_wifi_fail_count, WIFI_FAIL_THRESHOLD);
        goto deep_sleep;
    }
    s_wifi_fail_count = 0;  /* reset on success */
    ap_config_set_conn_failed(false);

    /* 7. NTP — only if needed (first boot or 24h elapsed) */
    if (need_ntp_sync()) {
        ntp_sync();
    } else {
        ESP_LOGI(TAG, "NTP skip, last sync %lld sec ago",
                 (long long)(get_unix_ms() / 1000LL - s_last_sync_unix));
    }

    if (!sensor_ok) {
        goto deep_sleep;
    }

    /* 8. Wait for BME280 measurement to complete (~50 ms) */
    vTaskDelay(pdMS_TO_TICKS(50));

    /* 9. MQTT-UDP client */
    mqttudp_client_init();

    /* 10. Measure and send */
    {
        sensor_data_t data;
        gpio_set_level(LED_GPIO, LED_ON);
        if (sensor_read(&data)) {
            mqttudp_send_sensor_data(&data, device_id, get_unix_ms());
        } else {
            ESP_LOGW(TAG, "Sensor read failed");
        }
        gpio_set_level(LED_GPIO, LED_OFF);
    }

deep_sleep:
    /* Shut down Wi-Fi before sleep to avoid wasting power */
    wifi_stop();

    /* Isolate GPIO to prevent leakage during deep sleep */
    gpio_set_level(LED_GPIO, LED_OFF);
    gpio_reset_pin(LED_GPIO);

    ESP_LOGI(TAG, "Going to deep sleep for %d min...", SLEEP_MINUTES);
    esp_sleep_enable_timer_wakeup(SLEEP_US);
    esp_deep_sleep_start();
}
