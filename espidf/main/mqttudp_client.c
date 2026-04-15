#include "mqttudp_client.h"

#include "esp_log.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <time.h>

static const char *TAG = "mqttudp";

/* ── Settings ────────────────────────────────────────────────── */
#define MQTTUDP_PORT       1883
#define MQTTUDP_BROADCAST  "255.255.255.255"

/*
 * Minimal MQTT PUBLISH packet (QoS 0):
 *  Byte 0    : 0x30  — PUBLISH, QoS 0
 *  Byte 1    : remaining length (< 128)
 *  Bytes 2-3 : topic length (big-endian)
 *  Bytes 4.. : topic string
 *  Bytes ..  : payload
 */
static int build_mqtt_publish(uint8_t *buf, size_t buf_size,
                               const char *topic, const char *payload)
{
    size_t topic_len   = strlen(topic);
    size_t payload_len = strlen(payload);
    size_t remain_len  = 2 + topic_len + payload_len;

    /* Encode remaining length (MQTT variable-length encoding, up to 4 bytes) */
    uint8_t remain_bytes[4];
    size_t  remain_count = 0;
    size_t  rl = remain_len;
    do {
        uint8_t byte = rl & 0x7F;
        rl >>= 7;
        if (rl > 0) byte |= 0x80;
        remain_bytes[remain_count++] = byte;
    } while (rl > 0 && remain_count < 4);

    if ((1 + remain_count + remain_len) > buf_size) {
        return -1;
    }

    size_t i = 0;
    buf[i++] = 0x30;  /* PUBLISH, QoS 0 */
    memcpy(&buf[i], remain_bytes, remain_count); i += remain_count;
    buf[i++] = (uint8_t)((topic_len >> 8) & 0xFF);
    buf[i++] = (uint8_t)(topic_len & 0xFF);
    memcpy(&buf[i], topic,   topic_len);   i += topic_len;
    memcpy(&buf[i], payload, payload_len); i += payload_len;

    return (int)i;
}

/* ── State ───────────────────────────────────────────────────── */
static int                sock = -1;
static struct sockaddr_in dest_addr;

/* ── Public functions ────────────────────────────────────────── */
void mqttudp_client_init(void)
{
    sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "socket() failed: errno=%d", errno);
        return;
    }

    int broadcast = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast));

    memset(&dest_addr, 0, sizeof(dest_addr));
    dest_addr.sin_family      = AF_INET;
    dest_addr.sin_port        = htons(MQTTUDP_PORT);
    dest_addr.sin_addr.s_addr = inet_addr(MQTTUDP_BROADCAST);

    ESP_LOGI(TAG, "MQTT-UDP ready → %s:%d", MQTTUDP_BROADCAST, MQTTUDP_PORT);
}

static void publish(const char *topic, const char *payload)
{
    if (sock < 0) {
        ESP_LOGE(TAG, "Socket not initialized");
        return;
    }

    uint8_t buf[512];
    int len = build_mqtt_publish(buf, sizeof(buf), topic, payload);
    if (len < 0) {
        ESP_LOGE(TAG, "Packet too large");
        return;
    }

    int sent = sendto(sock, buf, (size_t)len, 0,
                      (struct sockaddr *)&dest_addr, sizeof(dest_addr));
    if (sent < 0) {
        ESP_LOGE(TAG, "sendto failed: errno=%d", errno);
    } else {
        ESP_LOGI(TAG, "→ %s : %s", topic, payload);
    }
}

void mqttudp_send_sensor_data(const sensor_data_t *data,
                               const char *device_id,
                               int64_t unix_ms)
{
    /* Topic: weather/<device_id> as expected by server */
    char topic[48];
    snprintf(topic, sizeof(topic), "weather/%s", device_id);

    /* ts — UTC datetime string as expected by server */
    char ts_str[32] = "1970-01-01T00:00:00";
    if (unix_ms > 0) {
        time_t t = (time_t)(unix_ms / 1000);
        struct tm *tm_info = gmtime(&t);
        strftime(ts_str, sizeof(ts_str), "%Y-%m-%dT%H:%M:%S", tm_info);
    }

    /* Fields t/h/p/v and optionally vs */
    char payload[160];
#if CONFIG_SOLAR_ENABLED
    snprintf(payload, sizeof(payload),
        "{\"ts\":\"%s\",\"t\":%.1f,\"h\":%.1f,\"p\":%.1f,\"v\":%.2f,\"vs\":%.2f}",
        ts_str,
        data->temperature,
        data->humidity,
        data->pressure,
        data->voltage,
        data->voltage_solar);
#else
    snprintf(payload, sizeof(payload),
        "{\"ts\":\"%s\",\"t\":%.1f,\"h\":%.1f,\"p\":%.1f,\"v\":%.2f}",
        ts_str,
        data->temperature,
        data->humidity,
        data->pressure,
        data->voltage);
#endif

    publish(topic, payload);
}

void mqttudp_send_config(const char *device_id, const char *sensor_name)
{
    char topic[48];
    snprintf(topic, sizeof(topic), "weather/%s/config", device_id);

    char payload[320];
    snprintf(payload, sizeof(payload),
        "{"
        "\"fw\":\"espidf\","
        "\"sensor\":\"%s\","
        "\"sleep\":%d,"
        "\"led\":%d,"
#if CONFIG_LED_INVERTED
        "\"led_inv\":1,"
#else
        "\"led_inv\":0,"
#endif
        "\"i2c_sda\":%d,"
        "\"i2c_scl\":%d,"
        "\"bat_gpio\":%d"
#if CONFIG_SOLAR_ENABLED
        ",\"solar\":1,\"sol_gpio\":%d"
#else
        ",\"solar\":0"
#endif
        "}",
        sensor_name,
        CONFIG_SLEEP_MINUTES,
        CONFIG_LED_GPIO,
        CONFIG_I2C_SDA_GPIO,
        CONFIG_I2C_SCL_GPIO,
        CONFIG_BATTERY_ADC_GPIO
#if CONFIG_SOLAR_ENABLED
        , CONFIG_SOLAR_ADC_GPIO
#endif
    );

    publish(topic, payload);
}