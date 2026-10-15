"""HydroCon Raspberry Pi controller.

Run:  python -m hydrocon.main --config config.yaml [--dry-run]

Subscribes:
  nft/sensor_node/sensors      pH, EC, temperature, TDS, DO
  nft/dose_node/sensors        relay states, TDS2
  nft/growth_node/stream_url   camera stream address
  nft/growth_node/image        base64 JPEG (optional)
Publishes:
  nft/dose_node/relays         {"relay": "A", "action": "ON"}
  nft/growth_node/cmd          CAPTURE_SINGLE
  nft/controller/status        latest decision (retained), for dashboards
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import logging
import os
import queue
import signal
import threading
import time
from typing import Dict, Optional

import yaml

from .logic import Decision, Planner, Reading, SensorHistory, StageTracker
from .vision import grab_mjpeg_frame, load_classifier

log = logging.getLogger("hydrocon")

DOSE_RELAYS = ["A", "B", "PH_UP", "PH_DOWN"]
STAGES = ["seedling", "vegetative", "harvest"]


class Controller:
    def __init__(self, cfg: Dict, dry_run: bool):
        import paho.mqtt.client as mqtt

        self.cfg = cfg
        self.dry_run = dry_run
        self.topics = cfg["mqtt"]["topics"]
        self.history = SensorHistory()
        self.planner = Planner(cfg)
        self.stage = StageTracker(STAGES, cfg["growth"]["initial_stage"], cfg["growth"]["votes"])
        self.lock = threading.Lock()
        self.images: "queue.Queue[bytes]" = queue.Queue(maxsize=3)
        self.stream_url: Optional[str] = cfg["growth"].get("stream_url") or None
        self.dose_node_state: Dict = {}
        self.stop = threading.Event()

        self.classifier = None
        if cfg["growth"]["use_classifier"]:
            self.classifier = load_classifier(cfg["growth"]["model_path"], cfg["growth"]["class_names_path"])

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hydrocon_controller")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        os.makedirs(os.path.dirname(cfg["logging"]["csv_path"]) or ".", exist_ok=True)

    # ---------------- MQTT ----------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("Connected to MQTT broker")
        for key in ("sensor", "dose_status", "growth_stream", "growth_image"):
            client.subscribe(self.topics[key], qos=0)
        self.all_pumps_off()

    def _on_message(self, client, userdata, msg):
        try:
            if msg.topic == self.topics["growth_stream"]:
                self.stream_url = msg.payload.decode().strip()
                log.info("Growth node stream URL: %s", self.stream_url)
                return
            data = json.loads(msg.payload)
            if msg.topic == self.topics["sensor"]:
                r = Reading(t=time.time(), ph=float(data["ph"]), ec=float(data["ec"]),
                            temperature=float(data["temperature"]),
                            tds=data.get("tds"), do=data.get("do"))
                with self.lock:
                    self.history.add(r)
            elif msg.topic == self.topics["dose_status"]:
                self.dose_node_state = data
            elif msg.topic == self.topics["growth_image"] and "image" in data:
                try:
                    self.images.put_nowait(base64.b64decode(data["image"]))
                except queue.Full:
                    pass
        except (ValueError, KeyError, TypeError) as e:
            log.warning("Bad message on %s: %s", msg.topic, e)

    # ---------------- Pumps ----------------
    def set_relay(self, relay: str, on: bool) -> None:
        payload = json.dumps({"relay": relay, "action": "ON" if on else "OFF"})
        self.client.publish(self.topics["dose_command"], payload, qos=1)

    def all_pumps_off(self) -> None:
        for relay in DOSE_RELAYS:
            self.set_relay(relay, False)

    def run_dose(self, decision: Decision) -> None:
        gap = self.cfg["dosing"]["pump_gap_seconds"]
        for i, (relay, secs) in enumerate(decision.dose.pumps):
            if i > 0:
                time.sleep(gap)  # never let concentrated A and B meet in the line
            if self.stop.is_set():
                return
            log.info("DOSE %s ON for %.1fs", relay, secs)
            if self.dry_run:
                continue
            try:
                self.set_relay(relay, True)
                self.stop.wait(secs)
            finally:
                self.set_relay(relay, False)

    # ---------------- Growth stage ----------------
    def update_growth_stage(self) -> None:
        if self.classifier is None:
            return
        jpeg = None
        if self.stream_url:
            try:
                jpeg = grab_mjpeg_frame(self.stream_url)
            except Exception as e:
                log.warning("Could not grab camera frame: %s", e)
        if jpeg is None:
            try:
                jpeg = self.images.get_nowait()
            except queue.Empty:
                self.client.publish(self.topics["growth_cmd"], "CAPTURE_SINGLE")
                return
        try:
            label, conf = self.classifier.predict(jpeg)
        except Exception:
            log.exception("Classification failed")
            return
        if conf < self.cfg["growth"]["min_confidence"]:
            log.info("Growth prediction %s (%.0f%%) below confidence threshold", label, conf * 100)
            return
        before = self.stage.current
        self.stage.add_prediction(label)
        log.info("Growth prediction: %s (%.0f%%), stage = %s", label, conf * 100, self.stage.current)
        if self.stage.current != before:
            log.info("Growth stage changed %s -> %s; setpoints updated", before, self.stage.current)

    # ---------------- Logging ----------------
    def log_decision(self, d: Decision, now: float) -> None:
        path = self.cfg["logging"]["csv_path"]
        new = not os.path.exists(path)
        sp = self.cfg["setpoints"][d.stage] if d.stage else {}
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "stage", "ph", "ph_target", "ph_trend", "ec", "ec_target", "ec_trend",
                            "ec_fuzzy_out", "ph_fuzzy_out", "action", "pumps", "message", "warnings", "dry_run"])
            fmt = lambda v: "" if v is None else round(v, 4) + 0.0  # noqa: E731  (+0.0 avoids "-0.0")
            w.writerow([dt.datetime.fromtimestamp(now).isoformat(timespec="seconds"), d.stage,
                        fmt(d.ph), sp.get("ph"), fmt(d.ph_trend), fmt(d.ec), sp.get("ec"), fmt(d.ec_trend),
                        fmt(d.ec_out), fmt(d.ph_out), d.action,
                        ";".join(f"{r}:{s}" for r, s in d.dose.pumps) if d.dose else "",
                        d.message, " | ".join(d.warnings), self.dry_run])

    def publish_status(self, d: Decision, now: float) -> None:
        status = {
            "time": dt.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "stage": d.stage, "action": d.action, "message": d.message,
            "ph": d.ph, "ec": d.ec, "ph_trend": d.ph_trend, "ec_trend": d.ec_trend,
            "targets": self.cfg["setpoints"].get(d.stage), "warnings": d.warnings,
            "dry_run": self.dry_run,
        }
        self.client.publish(self.topics["status"], json.dumps(status), qos=0, retain=True)

    # ---------------- Main loop ----------------
    def run(self) -> None:
        m = self.cfg["mqtt"]
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password"))
        self.client.connect_async(m["host"], m["port"], keepalive=30)
        self.client.loop_start()
        log.info("HydroCon controller started%s, stage=%s", " (DRY RUN)" if self.dry_run else "", self.stage.current)

        interval = self.cfg["control"]["interval_seconds"]
        vision_every = self.cfg["growth"]["interval_seconds"]
        next_vision = time.time() + 30
        try:
            while not self.stop.is_set():
                now = time.time()
                if now >= next_vision:
                    next_vision = now + vision_every
                    self.update_growth_stage()

                with self.lock:
                    d = self.planner.decide(self.history, self.stage.current, now,
                                            dt.date.fromtimestamp(now).isoformat())
                for w in d.warnings:
                    log.warning(w)
                log.info("[%s] %s: %s", d.stage, d.action, d.message)
                if d.action == "dose":
                    self.planner.record_dose(d.dose, now)
                    self.log_decision(d, now)
                    self.publish_status(d, now)
                    self.run_dose(d)
                else:
                    self.log_decision(d, now)
                    self.publish_status(d, now)
                self.stop.wait(interval)
        finally:
            log.info("Shutting down: turning all dosing pumps OFF")
            self.all_pumps_off()
            time.sleep(0.5)
            self.client.loop_stop()
            self.client.disconnect()


def load_config(path: str) -> Dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for stage in STAGES:
        if stage not in cfg["setpoints"]:
            raise ValueError(f"config: missing setpoints for '{stage}'")
    if cfg["growth"]["initial_stage"] not in STAGES:
        raise ValueError(f"config: initial_stage must be one of {STAGES}")
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description="HydroCon fuzzy logic nutrient controller")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="Decide and log, but never switch pumps")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    cfg = load_config(args.config)
    ctl = Controller(cfg, dry_run=args.dry_run or cfg["control"].get("dry_run", False))

    def handle_signal(signum, frame):
        ctl.stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    ctl.run()


if __name__ == "__main__":
    main()
