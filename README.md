# HydroCon
IoT-driven hydroponic nutrient control system with fuzzy logic and image-based growth stage classification for Batavia lettuce.

# IoT-Driven Hydroponic Nutrient Control System

This project implements a fully automated nutrient and pH control system for NFT (Nutrient Film Technique) hydroponic farming. It uses an IoT-based architecture to monitor water quality parameters and plant growth, making intelligent dosing decisions based on sensor data and image classification.

## 🌱 Features

- 📷 ESP32-CAM-based image capture and growth stage classification (seedling, vegetative, harvest)
- 💧 Real-time monitoring of EC, pH, DO, TDS, and water temperature
- 🤖 Fuzzy logic controller for dynamic nutrient and pH dosing
- 📡 MQTT communication between ESP32 nodes and Raspberry Pi controller
- 📊 Growth stage model trained on 4,000+ Batavia lettuce images (HydroGrowNet)

## 🧠 System Architecture

- **Sensor Node:** Collects EC, DO, pH, and water temperature readings
- **Dose Node:** Controls 4 nutrient/pH pumps using fuzzy logic decisions
- **Growth Node:** Captures plant images and classifies growth stage
- **Raspberry Pi Controller:** Coordinates MQTT messages and decision logic

## 📂 Project Structure


## 📦 Dependencies

- ESP32 boards (DevKit and CAM)
- Raspberry Pi 4 (Python 3.9+)
- MQTT broker (e.g., Mosquitto)
- PyTorch, torchvision, scikit-learn, matplotlib
- Arduino IDE + required libraries (DFRobot, GreenPonik)

## 📸 Sample Output

- Confusion matrix
- Growth stage prediction output
- Real-time dashboard visualizations

## 🔗 Paper & Report

This repository supports an undergraduate engineering project report for Koç University ELEC 491.

## 📜 License

MIT License


