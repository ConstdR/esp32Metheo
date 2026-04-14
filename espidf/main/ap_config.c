#include "ap_config.h"

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "ap_config";

/* ── NVS keys ──────────────────────────────────────────────────── */
#define NVS_NAMESPACE   "wifi_cfg"
#define NVS_KEY_SSID    "ssid"
#define NVS_KEY_PASS    "password"
#define NVS_KEY_FAIL    "conn_fail"

/* ── LED blink task ────────────────────────────────────────────── */
static TaskHandle_t s_blink_task = NULL;

static void blink_task(void *arg)
{
    int gpio = (int)(intptr_t)arg;
    /* GPIO already configured as output in app_main */
    while (true) {
        gpio_set_level(gpio, 1);
        vTaskDelay(pdMS_TO_TICKS(200));
        gpio_set_level(gpio, 0);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

/* ── NVS operations ────────────────────────────────────────────── */
bool ap_config_load(wifi_creds_t *creds)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }

    size_t len = sizeof(creds->ssid);
    esp_err_t err_ssid = nvs_get_str(nvs, NVS_KEY_SSID, creds->ssid, &len);

    len = sizeof(creds->password);
    esp_err_t err_pass = nvs_get_str(nvs, NVS_KEY_PASS, creds->password, &len);

    nvs_close(nvs);

    if (err_ssid != ESP_OK || err_pass != ESP_OK) {
        return false;
    }

    /* Check that SSID is not empty */
    if (strlen(creds->ssid) == 0) {
        return false;
    }

    ESP_LOGI(TAG, "Loaded credentials for \"%s\"", creds->ssid);
    return true;
}

bool ap_config_save(const wifi_creds_t *creds)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        ESP_LOGE(TAG, "NVS open failed");
        return false;
    }

    nvs_set_str(nvs, NVS_KEY_SSID, creds->ssid);
    nvs_set_str(nvs, NVS_KEY_PASS, creds->password);
    esp_err_t err = nvs_commit(nvs);
    nvs_close(nvs);

    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Saved credentials for \"%s\"", creds->ssid);
    }
    return err == ESP_OK;
}

bool ap_config_erase(void)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return false;
    }
    nvs_erase_all(nvs);
    esp_err_t err = nvs_commit(nvs);
    nvs_close(nvs);
    ESP_LOGI(TAG, "Credentials erased");
    return err == ESP_OK;
}

void ap_config_set_conn_failed(bool failed)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) return;
    if (failed) {
        nvs_set_u8(nvs, NVS_KEY_FAIL, 1);
    } else {
        nvs_erase_key(nvs, NVS_KEY_FAIL);
    }
    nvs_commit(nvs);
    nvs_close(nvs);
}

bool ap_config_get_conn_failed(void)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) return false;
    uint8_t val = 0;
    esp_err_t err = nvs_get_u8(nvs, NVS_KEY_FAIL, &val);
    nvs_close(nvs);
    return (err == ESP_OK && val != 0);
}

/* ── URL decode helper ─────────────────────────────────────────── */
static void url_decode(char *dst, const char *src, size_t dst_size)
{
    size_t di = 0;
    for (size_t si = 0; src[si] && di < dst_size - 1; si++) {
        if (src[si] == '%' && src[si + 1] && src[si + 2]) {
            char hex[3] = { src[si + 1], src[si + 2], 0 };
            dst[di++] = (char)strtol(hex, NULL, 16);
            si += 2;
        } else if (src[si] == '+') {
            dst[di++] = ' ';
        } else {
            dst[di++] = src[si];
        }
    }
    dst[di] = '\0';
}

/* ── Parse query parameter ─────────────────────────────────────── */
static bool get_query_param(const char *query, const char *key,
                            char *value, size_t value_size)
{
    size_t key_len = strlen(key);
    const char *p = query;

    while (p) {
        if (strncmp(p, key, key_len) == 0 && p[key_len] == '=') {
            const char *val_start = p + key_len + 1;
            const char *val_end = strchr(val_start, '&');
            size_t val_len = val_end ? (size_t)(val_end - val_start)
                                     : strlen(val_start);
            if (val_len >= value_size) val_len = value_size - 1;

            char encoded[128] = {0};
            if (val_len >= sizeof(encoded)) val_len = sizeof(encoded) - 1;
            memcpy(encoded, val_start, val_len);
            encoded[val_len] = '\0';

            url_decode(value, encoded, value_size);
            return true;
        }
        p = strchr(p, '&');
        if (p) p++;
    }
    return false;
}

/* ── HTML page ─────────────────────────────────────────────────── */
static const char CONFIG_PAGE[] =
    "<!DOCTYPE html><html><head>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<style>"
    "body{font-family:sans-serif;max-width:400px;margin:40px auto;padding:0 20px}"
    "h1{color:#333}input[type=text],input[type=password]{width:100%%;padding:8px;"
    "margin:4px 0 16px;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}"
    "input[type=submit]{background:#2196F3;color:#fff;padding:10px 24px;border:none;"
    "border-radius:4px;cursor:pointer;font-size:16px}"
    "input[type=submit]:hover{background:#1976D2}"
    ".msg{padding:10px;margin:16px 0;border-radius:4px}"
    ".ok{background:#C8E6C9;color:#2E7D32}"
    ".err{background:#FFCDD2;color:#C62828}"
    "</style></head><body>"
    "<h1>&#9729; Weather Station</h1>"
    "%s"
    "<form action='/save' method='get'>"
    "<label>Wi-Fi SSID:</label>"
    "<input type='text' name='ssid' value='' required>"
    "<label>Password:</label>"
    "<input type='text' name='pass' value=''>"
    "<br><input type='submit' value='Save &amp; Reboot'>"
    "</form></body></html>";

/* ── HTTP handlers ─────────────────────────────────────────────── */
static esp_err_t root_handler(httpd_req_t *req)
{
    char page[sizeof(CONFIG_PAGE) + 256];
    char msg[192];

    if (ap_config_get_conn_failed()) {
        wifi_creds_t creds = {0};
        ap_config_load(&creds);
        snprintf(msg, sizeof(msg),
                 "<div class='msg err'>Connection to <b>%s</b> failed "
                 "(password: %s). Try again.</div>",
                 creds.ssid,
                 strlen(creds.password) > 0 ? creds.password : "<empty>");
        ap_config_set_conn_failed(false);  /* clear flag after showing */
    } else {
        snprintf(msg, sizeof(msg), "<p>Enter Wi-Fi credentials:</p>");
    }

    snprintf(page, sizeof(page), CONFIG_PAGE, msg);
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t save_handler(httpd_req_t *req)
{
    char query[256] = {0};
    wifi_creds_t creds = {0};
    char page[sizeof(CONFIG_PAGE) + 256];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) {
        snprintf(page, sizeof(page), CONFIG_PAGE,
                 "<div class='msg err'>Missing parameters.</div>");
        httpd_resp_set_type(req, "text/html");
        httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    if (!get_query_param(query, "ssid", creds.ssid, sizeof(creds.ssid)) ||
        strlen(creds.ssid) == 0)
    {
        snprintf(page, sizeof(page), CONFIG_PAGE,
                 "<div class='msg err'>SSID is required.</div>");
        httpd_resp_set_type(req, "text/html");
        httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    get_query_param(query, "pass", creds.password, sizeof(creds.password));

    if (ap_config_save(&creds)) {
        char msg[128];
        snprintf(msg, sizeof(msg),
                 "<div class='msg ok'>Saved! Rebooting to connect to <b>%s</b>...</div>",
                 creds.ssid);
        snprintf(page, sizeof(page), CONFIG_PAGE, msg);
        httpd_resp_set_type(req, "text/html");
        httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);

        /* Give browser time to receive response, then reboot */
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    } else {
        snprintf(page, sizeof(page), CONFIG_PAGE,
                 "<div class='msg err'>Failed to save. Try again.</div>");
        httpd_resp_set_type(req, "text/html");
        httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
    }

    return ESP_OK;
}

/* Captive portal: redirect all unknown requests to root */
static esp_err_t redirect_handler(httpd_req_t *req)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    httpd_resp_send(req, NULL, 0);
    return ESP_OK;
}

/* ── AP mode with HTTP server ──────────────────────────────────── */
void ap_config_start(uint32_t timeout_sec, const char *device_id)
{
    ESP_LOGI(TAG, "Starting AP configuration mode (timeout=%lus)", (unsigned long)timeout_sec);

    /* Start LED blinking */
    s_blink_task = NULL;
    xTaskCreate(blink_task, "blink", 2048,
                (void *)(intptr_t)CONFIG_LED_GPIO, 1, &s_blink_task);

    /* Configure AP */
    char ap_ssid[33];
    snprintf(ap_ssid, sizeof(ap_ssid), "Metheo_%s", device_id + 6);

    wifi_config_t wifi_config = {
        .ap = {
            .max_connection = 2,
            .authmode       = WIFI_AUTH_OPEN,
            .channel        = 1,
        },
    };
    strncpy((char *)wifi_config.ap.ssid, ap_ssid, sizeof(wifi_config.ap.ssid));
    wifi_config.ap.ssid_len = strlen(ap_ssid);

    esp_netif_create_default_wifi_ap();
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &wifi_config);
    esp_wifi_start();

    ESP_LOGI(TAG, "AP started: \"%s\" → http://192.168.4.1", ap_ssid);

    /* Start HTTP server */
    httpd_config_t http_config = HTTPD_DEFAULT_CONFIG();
    http_config.uri_match_fn = httpd_uri_match_wildcard;

    httpd_handle_t server = NULL;
    if (httpd_start(&server, &http_config) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start HTTP server");
        return;
    }

    /* Register handlers — order matters: specific first, wildcard last */
    const httpd_uri_t uri_root = {
        .uri = "/", .method = HTTP_GET, .handler = root_handler,
    };
    const httpd_uri_t uri_save = {
        .uri = "/save", .method = HTTP_GET, .handler = save_handler,
    };
    const httpd_uri_t uri_redirect = {
        .uri = "/*", .method = HTTP_GET, .handler = redirect_handler,
    };
    httpd_register_uri_handler(server, &uri_root);
    httpd_register_uri_handler(server, &uri_save);
    httpd_register_uri_handler(server, &uri_redirect);

    /* Wait for timeout — save_handler will reboot on success */
    if (timeout_sec > 0) {
        ESP_LOGI(TAG, "AP will shut down in %lu seconds", (unsigned long)timeout_sec);
        vTaskDelay(pdMS_TO_TICKS(timeout_sec * 1000));

        ESP_LOGW(TAG, "AP timeout — no config received, going to sleep");
        httpd_stop(server);
        esp_wifi_stop();

        if (s_blink_task) {
            vTaskDelete(s_blink_task);
            s_blink_task = NULL;
        }
        gpio_set_level(CONFIG_LED_GPIO, 0);
    } else {
        /* No timeout — block forever (shouldn't normally happen) */
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}
