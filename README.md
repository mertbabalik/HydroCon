# HydroCon

IoT-driven hydroponic nutrient control system with fuzzy logic and image-based growth stage classification for Batavia lettuce.

HydroCon automates nutrient and pH control for an NFT (Nutrient Film Technique) hydroponic setup. ESP32 nodes measure water quality and photograph the plants, a Raspberry Pi coordinates them over MQTT, and a fuzzy logic controller decides how much nutrient and pH solution to dose based on the sensor readings and the plants' current growth stage.

Undergraduate senior project for **Koç University ELEC 491**.

## Features

- 💧 Real-time monitoring of EC, pH, dissolved oxygen, TDS and water temperature
- 🤖 Fuzzy logic controller for dynamic nutrient (A/B) and pH up/down dosing
- 📷 ESP32-CAM image capture with growth stage classification (seedling, vegetative, harvest)
- 🧠 ResNet-18 classifier trained on 4,000+ Batavia lettuce images (HydroGrowNet)
- 📡 MQTT communication between the ESP32 nodes and the Raspberry Pi controller

## System architecture

```
                 ┌──────────────────────────┐
                 │  Raspberry Pi controller │
                 │  Mosquitto + fuzzy logic │
                 └────────────┬─────────────┘
                              │ MQTT (nft/...)
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────┴───────┐     ┌───────┴───────┐     ┌───────┴───────┐
│  Sensor node  │     │   Dose node   │     │  Growth node  │
│  ESP32 DevKit │     │  ESP32 DevKit │     │   ESP32-CAM   │
│ EC pH DO TDS  │     │ 4 dosing pumps│     │ camera, DHT11 │
│ water temp    │     │ + circulation │     │ ultrasonic    │
└───────────────┘     └───────────────┘     └───────────────┘
```

| Node | Board | Role |
|---|---|---|
| Sensor node | ESP32 DevKit | Reads EC, pH (GreenPonik / DFRobot), TDS (SEN0244), DO (SEN0237) and DS18B20 water temperature; controls grow light and pump relays |
| Dose node | ESP32 DevKit | Drives nutrient A, nutrient B, pH up, pH down and circulation pump relays; second TDS sensor |
| Growth node | ESP32-CAM (AI Thinker) | Captures plant images, streams video, reads air temperature/humidity (DHT11) and plant height (ultrasonic) |
| Controller | Raspberry Pi 4 | Runs the MQTT broker, growth stage model and fuzzy dosing logic |

## Repository structure

```
HydroCon/
├── firmware/
│   ├── sensor_node/        # Water quality sensing (ESP32)
│   ├── dose_node/          # Dosing pump control (ESP32)
│   └── growth_node/        # Camera + environment sensing (ESP32-CAM)
├── controller/             # Raspberry Pi fuzzy logic controller (Python)
│   ├── hydrocon/           # fuzzy engine, EC/pH controllers, MQTT service
│   ├── tests/
│   ├── simulate.py         # closed-loop simulation for tuning
│   └── config.yaml         # setpoints, pump limits, MQTT
├── ml/
│   ├── train_growth_stage_model.py
│   └── requirements.txt
├── LICENSE
└── README.md
```

## MQTT topics

| Topic | Direction | Content |
|---|---|---|
| `nft/sensor_node/sensors` | node → Pi | Water quality readings (JSON) |
| `nft/sensor_node/commands` | Pi → node | Relay / calibration commands |
| `nft/dose_node/sensors` | node → Pi | Dose node status and TDS |
| `nft/dose_node/relays` | Pi → node | Pump on/off commands |
| `nft/growth_node/sensors` | node → Pi | Air temperature, humidity, plant height |
| `nft/growth_node/image` | node → Pi | Base64-encoded plant image |
| `nft/growth_node/cmd` | Pi → node | Capture / stream commands |
| `nft/growth_node/stream_url` | node → Pi | Live camera stream URL |
| `nft/controller/status` | Pi → dashboards | Latest controller decision (retained) |

## Getting started

### 1. Firmware (ESP32 nodes)

1. Install the [Arduino IDE](https://www.arduino.cc/en/software) with the ESP32 board package.
2. Install the libraries: `PubSubClient`, `ArduinoJson`, `OneWire`, `DallasTemperature`, DFRobot ESP pH/EC (GreenPonik), `DFRobot_DHT11`.
3. In each node folder, copy `secrets.example.h` to `secrets.h` and fill in your Wi‑Fi name, password and the Raspberry Pi's IP address. `secrets.h` is git-ignored.
4. Open the node's `.ino` file, select the board (ESP32 Dev Module, or AI Thinker ESP32-CAM for the growth node) and upload.

### 2. Growth stage model

```bash
cd ml
pip install -r requirements.txt
python train_growth_stage_model.py --data-dir path/to/batavia_dataset_labeled --output-dir models
```

The dataset should be in `ImageFolder` format, with one sub-folder per growth stage. The script saves model checkpoints, `class_names.json`, a confusion matrix and the training loss curve to the output folder.

### 3. Controller (Raspberry Pi)

Install Mosquitto on the Pi (`sudo apt install mosquitto mosquitto-clients`) and make sure every node's `MQTT_SERVER` points to the Pi's IP address. Then set up the fuzzy logic controller:

```bash
cd controller
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m hydrocon.main --config config.yaml --dry-run
```

See [controller/README.md](controller/README.md) for how the fuzzy rules work, tuning, the simulator and running it as a service.

## License

MIT License. See [LICENSE](LICENSE).
