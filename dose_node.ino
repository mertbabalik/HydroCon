#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

// === WiFi Configuration ===
const char* ssid = "Dilmersu_EXT";
const char* password = "272202mbd";

// === MQTT Configuration ===
const char* mqtt_server = "192.168.1.202";
const int mqtt_port = 1883;
const char* mqtt_pub_topic = "nft/dose_node/sensors";
const char* mqtt_sub_topic = "nft/dose_node/relays";

WiFiClient espClient;
PubSubClient client(espClient);

// === Relay Pins (HIGH = ON) ===
#define RELAY_A        5
#define RELAY_B        18
#define RELAY_PH_UP    19
#define RELAY_PH_DOWN  21
#define RELAY_PUMP     22
const int relays[] = {RELAY_A, RELAY_B, RELAY_PH_UP, RELAY_PH_DOWN, RELAY_PUMP};
const char* relayNames[] = {"A", "B", "PH_UP", "PH_DOWN", "PUMP"};

// === TDS Sensor (TDS2) ===
#define TDS2_PIN 34
const int TDS_SAMPLES = 10;
float tdsBuffer[TDS_SAMPLES];
int tdsIndex = 0;

// === Timing ===
unsigned long lastReadTime = 0;
const unsigned long readInterval = 6000;

// === WiFi Setup ===
void setup_wifi() {
  WiFi.begin(ssid, password);
  Serial.print("🔌 Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ WiFi connected.");
}

// === MQTT Callback ===
void mqtt_callback(char* topic, byte* payload, unsigned int length) {
  payload[length] = '\0';
  StaticJsonDocument<200> doc;
  if (deserializeJson(doc, payload)) return;

  String relay = doc["relay"];
  String action = doc["action"];

  for (int i = 0; i < 5; i++) {
    if (relay.equalsIgnoreCase(relayNames[i])) {
      digitalWrite(relays[i], action == "ON" ? HIGH : LOW);
      Serial.printf("⚙️ Relay %s set to %s\n", relayNames[i], action.c_str());
    }
  }
}

// === MQTT Reconnect ===
void reconnect() {
  while (!client.connected()) {
    String clientId = "dose_node_" + String(random(0xffff), HEX);
    if (client.connect(clientId.c_str())) {
      client.subscribe(mqtt_sub_topic);
      Serial.println("📡 Subscribed to relay control topic.");
    } else {
      delay(5000);
    }
  }
}

// === TDS Reading ===
float rawTDSReading() {
  int analogValue = analogRead(TDS2_PIN);
  float voltage = analogValue * (3.3 / 4095.0);
  return (133.42 * pow(voltage, 3) - 255.86 * voltage * voltage + 857.39 * voltage) * 0.5;
}

float getSmoothedTDS() {
  float reading = rawTDSReading();
  tdsBuffer[tdsIndex] = reading;
  tdsIndex = (tdsIndex + 1) % TDS_SAMPLES;
  float sum = 0;
  for (int i = 0; i < TDS_SAMPLES; i++) sum += tdsBuffer[i];
  return sum / TDS_SAMPLES;
}

// === Publish Sensor Data ===
void publishSensorData() {
  float tds_ppm = getSmoothedTDS();

  StaticJsonDocument<512> doc;
  doc["node_id"] = "dose_node";
  doc["TDS2"] = round(tds_ppm);

  // Include relay states
  for (int i = 0; i < 5; i++) {
    doc["relay_" + String(relayNames[i])] = digitalRead(relays[i]) == HIGH ? "ON" : "OFF";
  }

  // Timestamp
  char ts[25];
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) {
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    doc["iso_timestamp"] = ts;
  }

  // Publish JSON
  char message[512];
  serializeJson(doc, message);
  client.publish(mqtt_pub_topic, message);
  Serial.println("📤 Sent MQTT Payload:");
  Serial.println(message);
}

// === Arduino Setup ===
void setup() {
  Serial.begin(115200);
  Serial.println("🚀 Starting Dose Node...");

  for (int i = 0; i < 5; i++) {
    pinMode(relays[i], OUTPUT);
    digitalWrite(relays[i], LOW);
  }

  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqtt_callback);
  configTime(0, 0, "pool.ntp.org");

  // Prime TDS buffer
  float initial = rawTDSReading();
  for (int i = 0; i < TDS_SAMPLES; i++) tdsBuffer[i] = initial;
}

// === Main Loop ===
void loop() {
  if (!client.connected()) reconnect();
  client.loop();

  if (millis() - lastReadTime > readInterval) {
    lastReadTime = millis();
    publishSensorData();
  }
}
