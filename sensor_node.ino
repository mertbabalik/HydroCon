/*
  HYCON SENSOR NODE - FINAL VERSION (SEN0244 TDS + SEN0237 DO)
  -------------------------------------------------------------
  - GreenPonik EC and pH with calibration support
  - Gravity TDS (SEN0244) with temperature compensation
  - Gravity DO (SEN0237) with voltage and lookup
  - Waterproof DS18B20 temperature sensor
  - MQTT data + full relay control
  - Serial output of sent MQTT messages
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ArduinoJson.h>
#include <EEPROM.h>
#include <time.h>
#include "DFRobot_ESP_PH.h"
#include "DFRobot_ESP_EC.h"

#define WIFI_SSID     "Dilmersu_EXT"
#define WIFI_PASS     "272202mbd"
#define MQTT_SERVER   "192.168.1.202"
#define MQTT_PORT     1883
#define PUB_TOPIC     "nft/sensor_node/sensors"
#define SUB_TOPIC     "nft/sensor_node/commands"

WiFiClient espClient;
PubSubClient client(espClient);

// === Pins (ESP32 30-pin) ===
#define DS18B20_PIN   4
#define PH_PIN        34
#define EC_PIN        35
#define DO_PIN        32
#define TDS_PIN       33
#define RELAY_LED     22
#define RELAY_PUMP1   23

OneWire oneWire(DS18B20_PIN);
DallasTemperature tempSensor(&oneWire);
DFRobot_ESP_PH ph;
DFRobot_ESP_EC ec;

float temperature = 25.0;
float phVal = 0.0, ecVal = 0.0, doVal = 0.0, tdsVal = 0.0;
float voltagePH = 0.0, voltageEC = 0.0, voltageDO = 0.0, voltageTDS = 0.0;

float getTemp() {
  tempSensor.requestTemperatures();
  return tempSensor.getTempCByIndex(0);
}

float readDO(float voltage, int tempC) {
  const uint16_t DO_Table[41] = {
    14460,14220,13820,13440,13090,12740,12420,12110,11810,11530,
    11260,11010,10770,10530,10300,10080,9860,9660,9460,9270,
    9080,8900,8730,8570,8410,8250,8110,7960,7820,7690,
    7560,7430,7300,7180,7070,6950,6840,6730,6630,6530,6410
  };
  uint16_t Vsat = 1600 + 35L * tempC - 875;
  return (voltage * DO_Table[tempC] / Vsat) / 1000.0;
}

float readTDS(float voltage, float tempC) {
  float comp = 1.0 + 0.02 * (tempC - 25.0);
  float volt = voltage / comp;
  return (133.42 * pow(volt, 3) - 255.86 * pow(volt, 2) + 857.39 * volt) * 0.5;
}

void mqtt_callback(char* topic, byte* payload, unsigned int length) {
  payload[length] = 0;
  String cmd = String((char*)payload);
  Serial.println("📩 MQTT Command: " + cmd);

  if (cmd == "LED_ON") digitalWrite(RELAY_LED, HIGH);
  else if (cmd == "LED_OFF") digitalWrite(RELAY_LED, LOW);
  else if (cmd == "PUMP1_ON") digitalWrite(RELAY_PUMP1, HIGH);
  else if (cmd == "PUMP1_OFF") digitalWrite(RELAY_PUMP1, LOW);
  else if (cmd == "CAL_PH_4") ph.calibration(voltagePH, temperature, (char*)"CAL,4");
  else if (cmd == "CAL_PH_7") ph.calibration(voltagePH, temperature, (char*)"CAL,7");
  else if (cmd == "PH_CLEAR") ph.calibration(voltagePH, temperature, (char*)"CAL,CLEAR");
  else if (cmd == "CAL_EC_1413") ec.calibration(voltageEC, temperature, (char*)"CAL,1413");
  else if (cmd == "CAL_EC_12880") ec.calibration(voltageEC, temperature, (char*)"CAL,12880");
  else if (cmd == "EC_CLEAR") ec.calibration(voltageEC, temperature, (char*)"CAL,CLEAR");
}

void publishSensorData() {
  StaticJsonDocument<512> doc;
  doc["node_id"] = "sensor_node";
  doc["temperature"] = temperature;
  doc["ph"] = phVal;
  doc["ec"] = ecVal;
  doc["tds"] = tdsVal;
  doc["do"] = doVal;
  doc["relay_led"] = digitalRead(RELAY_LED);
  doc["relay_pump1"] = digitalRead(RELAY_PUMP1);

  char ts[25];
  struct tm timeinfo;
  if (getLocalTime(&timeinfo)) strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  else strcpy(ts, "1970-01-01T00:00:00Z");
  doc["iso_timestamp"] = ts;

  char payload[512];
  serializeJson(doc, payload);
  client.publish(PUB_TOPIC, payload);

  Serial.println("📤 MQTT Sent:");
  Serial.println(payload);
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_LED, OUTPUT); digitalWrite(RELAY_LED, LOW);
  pinMode(RELAY_PUMP1, OUTPUT); digitalWrite(RELAY_PUMP1, LOW);
  EEPROM.begin(32);
  tempSensor.begin();
  ph.begin();
  ec.begin();

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("✅ WiFi connected.");
  configTime(0, 0, "pool.ntp.org");

  client.setServer(MQTT_SERVER, MQTT_PORT);
  client.setCallback(mqtt_callback);
}

void loop() {
  if (!client.connected()) {
    while (!client.connected()) {
      if (client.connect("sensor_node")) {
        client.subscribe(SUB_TOPIC);
        Serial.println("📡 MQTT Subscribed.");
      } else {
        delay(3000);
      }
    }
  }

  client.loop();

  static unsigned long last = 0;
  if (millis() - last > 6000) {
    last = millis();

    temperature = getTemp();
    voltagePH = analogRead(PH_PIN) * 3.3 / 4095.0;
    voltageEC = analogRead(EC_PIN) * 3.3 / 4095.0;
    voltageDO = analogRead(DO_PIN) * 3.3 / 4095.0;
    voltageTDS = analogRead(TDS_PIN) * 3.3 / 4095.0;

    phVal = ph.readPH(voltagePH, temperature);
    ecVal = ec.readEC(voltageEC, temperature);
    doVal = readDO(voltageDO, (int)temperature);
    tdsVal = readTDS(voltageTDS, temperature);

    publishSensorData();
  }
}
