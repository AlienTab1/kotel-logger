from __future__ import annotations

import json
import logging
import time

import paho.mqtt.client as mqtt

from .config import secret


def discovery(cfg, mapping, device_id, stale_s):
    prefix = cfg["prefix"]
    device = {"identifiers": [f"thematronic_{device_id}"], "name": f"Kotel {device_id}", "manufacturer": "Thematronic"}
    for name, meta in mapping.items():
        if not meta.get("publish", False):
            continue
        component = meta.get("component", "sensor")
        uid = f"thematronic_{device_id}_{name}"
        config = {
            "name": meta.get("name", name), "unique_id": uid,
            "default_entity_id": f"{component}.{uid}", "device": device,
            "state_topic": prefix + "/state", "qos": 1,
            "availability_mode": "all",
            "availability": [
                {"topic": prefix + "/process"}, {"topic": prefix + "/availability"},
                {"topic": prefix + "/state", "value_template":
                 "{{ 'online' if value_json.statuses.get('" + name + "', {}).get('valid', false) else 'offline' }}"},
            ],
        }
        value = "value_json['values'].get('" + name + "')"
        if component == "binary_sensor":
            config.update(value_template="{{ 'ON' if " + value + " == true else 'OFF' }}",
                          payload_on="ON", payload_off="OFF")
        else:
            config.update(value_template="{{ " + value + " }}", expire_after=int(stale_s))
        for key in ("unit_of_measurement", "device_class", "state_class", "entity_category", "icon"):
            if key in meta:
                config[key] = meta[key]
        yield f"{cfg['discovery_prefix']}/{component}/{uid}/config", config
    for name, template, device_class in (
        ("received_at", "{{ value_json.received_at }}", "timestamp"),
    ):
        uid = f"thematronic_{device_id}_{name}"
        yield f"{cfg['discovery_prefix']}/sensor/{uid}/config", {
            "name": "Poslední příjem dat", "unique_id": uid, "default_entity_id": f"sensor.{uid}",
            "device": device, "state_topic": prefix + "/state", "value_template": template,
            "device_class": device_class, "entity_category": "diagnostic", "expire_after": int(stale_s),
            "availability_mode": "all", "availability": [{"topic": prefix + "/process"}, {"topic": prefix + "/availability"}],
        }


class Publisher:
    def __init__(self, cfg, mapping, device_id, stop, telemetry):
        self.cfg, self.mapping, self.device_id, self.stop = cfg, mapping, device_id, stop
        self.telemetry = telemetry
        self.connected = False
        self.refresh = False
        self.sent_id = None
        self.status = "disabled" if not cfg["enabled"] else "connecting"

    def _publish(self, client, suffix, data, retain=False):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False) if isinstance(data, dict) else data
        info = client.publish(self.cfg["prefix"] + "/" + suffix, payload, qos=1, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("mqtt_publish_failed")

    def _connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            self.connected = False
            return
        self.connected, self.refresh = True, True
        self.sent_id = None
        client.subscribe("homeassistant/status", qos=1)
        self._publish(client, "availability", "offline", True)
        self._publish(client, "process", "online", True)

    def _message(self, client, userdata, message):
        if message.topic == "homeassistant/status" and message.payload == b"online":
            self.refresh = True

    def _disconnect(self, client, userdata, flags, reason, properties):
        self.connected = False

    def _send(self, client, previous_available):
        sample = self.telemetry.current()
        if self.refresh:
            for topic, config in discovery(self.cfg, self.mapping, self.device_id, self.telemetry.stale_s):
                info = client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("mqtt_discovery_failed")
        if sample and (self.refresh or sample["sample_id"] != self.sent_id):
            # MQTT carries normalized values and quality; raw is in the local diagnostic snapshot.
            self._publish(client, "state", {k: v for k, v in sample.items() if k != "raw"})
            self.sent_id = sample["sample_id"]
        available = bool(sample)
        if self.refresh or available != previous_available:
            self._publish(client, "availability", "online" if available else "offline", True)
        self.refresh = False
        return available

    def run(self):
        if not self.cfg["enabled"]:
            return
        delay = 1
        while not self.stop.is_set():
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"kotel-reader-{self.device_id}", clean_session=True)
            client.connect_timeout = 5
            client.max_queued_messages_set(max(200, len(self.mapping) * 2))
            client.will_set(self.cfg["prefix"] + "/process", "offline", qos=1, retain=True)
            client.on_connect, client.on_message, client.on_disconnect = self._connect, self._message, self._disconnect
            if self.cfg["username"]:
                client.username_pw_set(self.cfg["username"], secret("MQTT_PASSWORD"))
            if self.cfg["tls"]:
                client.tls_set(ca_certs=self.cfg["ca_file"])
            self.connected = False
            try:
                client.connect(self.cfg["host"], self.cfg["port"], keepalive=20)
                started, previous_available = time.monotonic(), False
                while not self.stop.is_set():
                    if client.loop(timeout=0.2) != mqtt.MQTT_ERR_SUCCESS:
                        raise ConnectionError()
                    if self.connected:
                        self.status = "connected"
                        previous_available = self._send(client, previous_available)
                        if time.monotonic() - started > 60:
                            delay = 1
                    elif time.monotonic() - started > 5:
                        raise ConnectionError()
                    self.stop.wait(0.05)
                if self.connected:
                    self._publish(client, "availability", "offline", True)
                    self._publish(client, "process", "offline", True)
                    deadline = time.monotonic() + 2
                    while client.want_write() and time.monotonic() < deadline:
                        client.loop(timeout=0.1)
            except Exception:
                self.status = "retrying"
                logging.warning("mqtt_unavailable")
            finally:
                client.disconnect()
                client.loop(timeout=0.1)
                self.connected = False
            # Discard this client and its outgoing queue: never replay stale state on reconnect.
            self.stop.wait(delay)
            delay = min(delay * 2, 300)
