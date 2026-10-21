# Boiler logger

The repository contains two independent Python projects:

- **[web-logger](web-logger/README.md)**: existing Thematronic web â†’ Prometheus / MQTT collector.
  Run Docker and Compose commands from this directory. Moving the source does not update a running deployment.
- **[usb-logger](usb-logger/README.md)**: JER-1 USB analysis, offline decoder,
  diagnostic exporter and Raspberry Pi 1 service deployment. No actuator commands.

Captured data is in `usb-logger/log/`; findings are in `usb-logger/ANALYSIS.md`.
The root `.venv` remains a local development environment and is not distributed.
Project documentation, comments and display labels are written in English.
Protocol identifiers, exact vendor response strings and original evidence retain their original spelling.
