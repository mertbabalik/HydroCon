// HYCON GROWTH NODE
#include <WiFi.h>
#include <PubSubClient.h>
#include "DFRobot_DHT11.h"
#include "esp_camera.h"
#include <base64.h>
#include <ArduinoJson.h>
#include <WebServer.h>

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

// Pin configuration
#define DHTPIN 14  // DHT sensor connected to GPIO14
#define TRIG_PIN 12
#define ECHO_PIN 13

// MQTT & WiFi
const char* ssid = "HydroCon";
const char* password = "hydro123";
const char* mqtt_server = "172.20.10.5";
const int mqtt_port = 1883;

const char* MQTT_SENSOR = "nft/growth_node/sensors";
const char* MQTT_IMAGE  = "nft/growth_node/image";
const char* MQTT_CMD    = "nft/growth_node/cmd";
const char* MQTT_STREAM = "nft/growth_node/stream_url";

WiFiClient espClient;
PubSubClient client(espClient);
WebServer server(80);
DFRobot_DHT11 DHT;

unsigned long lastSensorTime = 0;
const long sensorInterval = 6000;

bool captureSequence = false;
int sequenceIndex = 0;
unsigned long lastImageSent = 0;

String getISOTime() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) return "1970-01-01T00:00:00Z";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buf);
}

void sendImageMQTT(int imageNum = -1) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) return;
  String b64 = base64::encode(fb->buf, fb->len);
  StaticJsonDocument<1024> doc;
  doc["node_id"] = "growth_node";
  doc["iso_timestamp"] = getISOTime();
  if (imageNum >= 0) doc["image_num"] = imageNum;
  doc["image"] = b64;
  String out;
  serializeJson(doc, out);
  client.publish(MQTT_IMAGE, out.c_str());
  esp_camera_fb_return(fb);
}

void sendSensorData() {
  DHT.read(DHTPIN);
  float temp = isnan(DHT.temperature) ? 25.0 : DHT.temperature;
  float hum  = isnan(DHT.humidity)   ? 60.0 : DHT.humidity;

  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  float dist = duration * 0.034 / 2.0;
  float height = (dist > 0 && dist < 400) ? 25.0 - dist : 0.0;

  StaticJsonDocument<300> doc;
  doc["node_id"] = "growth_node";
  doc["tempC"] = temp;
  doc["humidity"] = hum;
  doc["distance_cm"] = dist;
  doc["height_cm"] = height;
  doc["iso_timestamp"] = getISOTime();

  char msg[300];
  serializeJson(doc, msg);
  client.publish(MQTT_SENSOR, msg);
}

void setupWebStream() {
  server.on("/stream", HTTP_GET, []() {
    WiFiClient client = server.client();
    client.println("HTTP/1.1 200 OK");
    client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
    client.println();
    while (client.connected()) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (!fb) continue;
      client.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
      client.write(fb->buf, fb->len);
      client.println();
      esp_camera_fb_return(fb);
      delay(100);
    }
  });
  server.begin();
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  payload[length] = '\0';
  String cmd = String((char*)payload);
  if (cmd == "CAPTURE_SINGLE") {
    sendImageMQTT();
  } else if (cmd == "CAPTURE_SEQUENCE") {
    captureSequence = true;
    sequenceIndex = 0;
    lastImageSent = millis();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  configTime(0, 0, "pool.ntp.org");

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 10;
  config.fb_count = 1;
  esp_camera_init(&config);

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqttCallback);
  client.connect("esp32_growth_node");
  client.subscribe(MQTT_CMD);

  setupWebStream();

  // Send stream IP to MQTT
  IPAddress ip = WiFi.localIP();
  String streamURL = "http://" + ip.toString() + "/stream";
  client.publish(MQTT_STREAM, streamURL.c_str());
}

void loop() {
  if (!client.connected()) {
    client.connect("esp32_growth_node");
    client.subscribe(MQTT_CMD);
  }
  client.loop();
  server.handleClient();

  unsigned long now = millis();
  if (now - lastSensorTime > sensorInterval) {
    lastSensorTime = now;
    sendSensorData();
  }

  if (captureSequence && now - lastImageSent > 6000) {
    lastImageSent = now;
    sendImageMQTT(sequenceIndex);
    sequenceIndex++;
    if (sequenceIndex >= 10) {
      captureSequence = false;
    }
  }
}
