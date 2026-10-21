# kotel-reader

**Boiler telemetry from Thematronic to Prometheus and Home Assistant. One Docker container on TrueNAS.**

![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-linux%2Famd64-2496ED?logo=docker&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-metrics-E6522C?logo=prometheus&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-Home_Assistant-41BDF5)

`kotel-reader` reads the Thematronic website every 15 seconds, exposes current metrics
for Prometheus, and publishes current values over MQTT for Home Assistant.
It is **read-only**: it does not control the boiler or change its settings.

[TrueNAS Apps / YAML import](docs/truenas-apps.md) · [Operations guide](docs/operations.md) · [Configuration](config/config.example.yaml) · [GitHub setup](docs/github.md)

The documentation and default sensor labels are in English.

## How it works

```mermaid
flowchart LR
    Web[Thematronic] -->|HTTPS · every 15 s| Reader
    subgraph Container[Docker container on TrueNAS]
        Reader[Python collector] --> Metrics[HTTP /metrics]
        Reader --> Publisher[MQTT publisher]
        Reader --> State[Local state and latest diagnostic snapshot]
    end
    Prometheus -->|scrape · every 15 s| Metrics
    Grafana -->|PromQL queries| Prometheus
    Publisher --> Broker[MQTT broker]
    Broker --> HA[Home Assistant]
```

**Prometheus stores the history; Grafana queries Prometheus.** The collector does not
need PostgreSQL, database roles, SQL migrations, or a historical delivery queue.
Prometheus and the MQTT broker run separately from the collector.

## Features

- **Prometheus metrics:** temperatures, durations, numeric values, neutral boolean states, and collector diagnostics at `/metrics`.
- **Home Assistant:** MQTT Discovery with stable entity IDs, English labels, and availability for each signal.
- **Freshness handling:** stale, missing, or disconnected readings are omitted from metrics and marked unavailable in HA.
- **Independent outputs:** MQTT outages or missing Prometheus scrapes do not stop collection.
- **Session management:** cooldowns after logout, retry backoff, and local `status`, `pause`, and `resume` commands.
- **Persistent local state:** pauses and cooldowns survive restarts; one diagnostic snapshot preserves the latest raw response.
- **Simple deployment:** YAML configuration, separate credentials, a TrueNAS Apps manifest, and Grafana/HA dashboards.

**There is no backfill.** During a Prometheus outage, intervening readings are not
stored for later delivery. After recovery, Prometheus scrapes the current valid sample.
Text fields and unmapped signals remain in the latest diagnostic snapshot, not in
Prometheus history. Logs can be collected separately by your existing logging stack
(for example, Loki).

## Data quality

The exporter recomputes availability on every scrape. After a pause, confirmed logout,
or the configured reception age limit (60 seconds by default), it omits measurement
series entirely. It never substitutes zero or revives a stored snapshot after restart.
Scraping `/metrics` never triggers a login or a read from Thematronic.

Only explicitly mapped signals with `prometheus: true` are exported. The separate
`publish: true` setting enables their Home Assistant entities. Unknown text values,
sample UUIDs, and raw responses are never used as Prometheus labels.

The HTTP reception time **is not the controller's measurement time**. The known API
response does not establish source freshness, even when HTTP requests succeed.

## Running through TrueNAS Apps

1. Push the project to GitHub, then run **Actions → Publish container image → Run workflow**.
2. Prepare your dataset, [configuration](config/config.example.yaml), [signal mappings](config/signals.yaml), and three credential files.
3. In **Apps → Install via YAML**, paste [truenas-app.yaml](deploy/truenas-app.yaml).
4. Replace the image tag, dataset paths, NAS LAN IP, and published metrics port with your actual settings.
5. Add the [scrape job](deploy/prometheus-scrape.yaml) to your existing Prometheus configuration.
6. Import the [Grafana dashboard](deploy/grafana-dashboard.json), selecting your Prometheus data source.

Follow the [TrueNAS Apps guide](docs/truenas-apps.md) for permissions, image access,
verification, and updates. Port `9108` is the example metrics port; verify that it is
free and allow access from Prometheus on your trusted network. The endpoint has no
built-in authentication or TLS and is not intended for Internet exposure.

## Docker Compose alternative

Copy `config/config.example.yaml` to `config/config.yaml` and `.env.example` to `.env`.
Configure MQTT, your data directory, the NAS LAN bind address, and the three secret
files. UID/GID `10001:10001` must be able to read configuration/secrets and write `/data`.

```sh
docker compose build
docker compose run --rm collector validate
docker compose up -d collector
docker compose logs --tail=100 -f collector
```

There is no PostgreSQL setup or migration step.

## Operations

```sh
docker compose exec collector kotel-reader status
docker compose exec collector kotel-reader pause --minutes 30
docker compose exec collector kotel-reader resume
docker compose exec collector kotel-reader snapshot
```

`snapshot` prints the last received raw and normalized sample for diagnostics, which
may be old. It does not publish that sample. Restart after YAML changes and run only
one collector per Thematronic account.

## Monitoring files

| File | Purpose |
|---|---|
| [Prometheus scrape job](deploy/prometheus-scrape.yaml) | Add the collector to your existing Prometheus |
| [Grafana dashboard](deploy/grafana-dashboard.json) | Temperatures, signal availability, session state, and error rates |
| [Grafana data source](deploy/grafana-datasource.yaml) | Optional Prometheus provisioning example |
| [Home Assistant dashboard](deploy/ha-kotelna.yaml) | Boiler room page without HACS |

Prometheus controls retention and storage. MQTT remains independent of Prometheus
and uses non-retained state messages, retained Discovery, and availability/LWT.

## Validation and commissioning

Version **0.2.1** fixes double decompression of gzip/deflate responses, which could
report `network_error` immediately after receiving the login page with HTTP 200.
HTTP failures now include a sanitized request stage and exception type in logs and
`status.last_http_error`. See the [operations guide](docs/operations.md) for troubleshooting.

Tests cover HTTP authentication, session timing, state persistence, MQTT templates,
metric types, stale/missing readings, and an actual HTTP scrape on a loopback server.
An isolated container suite also checks a real Prometheus scrape, dashboard PromQL,
and a real MQTT broker. See the workflow results in GitHub Actions for container checks.
Docker was unavailable during local development; container deployment and dashboard
rendering on the actual NAS still require verification.

No live Thematronic account has been accessed. During commissioning, verify:

- Whether `DeviceSignalReload` is needed and how it should be timed; default `read_only` does not call it.
- Server session lifetime; it is unknown by default, with a 300-second cooldown after logout.
- Authentication error responses and the physical meaning/polarity of ambiguous signals.
- TrueNAS networking, Prometheus target health, and the dashboards in real Grafana/HA instances.

## Development

```sh
python -m venv .venv
# Activate .venv for your operating system.
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Tests block external network access; the HTTP endpoint test permits loopback only.
For the isolated Prometheus and MQTT integration suite:

```sh
docker build -t kotel-reader:0.2.1 .
docker compose -p kotel-reader-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kotel-reader-test -f compose.test.yaml down -v
```

## Upgrading from version 0.1

Use the new configuration example: remove `postgres` and `queue`, move `mqtt.stale_s`
to `telemetry.stale_s`, add `prometheus`, and update the container's secrets and port
mapping. Old configurations fail validation with an explicit upgrade message.

Existing PostgreSQL data and any old `queue.sqlite` are left untouched. The collector
imports cooldown/pause state from the old file once, but does not replay its historical
samples. Back up old history before retiring the old deployment. See the
[operations guide](docs/operations.md) for details.

## Project structure

```text
kotel_reader/   Collector, sessions, local state, Prometheus, MQTT, and CLI
config/         Example configuration and signal mappings
deploy/         TrueNAS manifest, scrape job, and dashboards
tests/          Offline, loopback, and container integration tests
docs/           Operations, TrueNAS Apps, and GitHub guides
tools/          Grafana dashboard generator
```

### Extended temperature and state mapping

The default `config/signals.yaml` includes room temperatures (`dev_prost_z1/2`), zone water targets, boiler 2, secondary storage, solar temperature, source maxima and additional raw operating states. Both supplied Temperatures/States captures contain the same 130 signal names. The collector already requests the complete `DeviceSignal` array; no additional page polling is introduced. Confirm that the live collector snapshot contains these signals before assuming the browser and collector responses are identical.

Maxima use `kotel_maximum_temperature_celsius`; their reset period is unknown. Disconnected sensors and `---` placeholders remain unavailable, never zero. Raw boolean polarity and numeric mode codes are not interpreted as alarms or translated modes. String modes are published through MQTT only. Reserved inputs/outputs and signals with uncertain units remain in the diagnostic snapshot.

**Existing TrueNAS installation:** back up and merge the updated `config/signals.yaml` into your mounted configuration directory, preserving local overrides, then restart kotel-logger. Updating the image alone does not replace a bind-mounted configuration. The mapping works with version 0.2.1. Reimport `deploy/grafana-dashboard.json` for the additional panels; its default Prometheus job is `kotel-logger`. Existing entity identifiers and metric names are preserved.

### 0.2.2: incident diagnostics and optional Loki

Existing 0.2.x configuration, `signals.yaml`, metrics, MQTT IDs and `state.sqlite` remain compatible. No database migration is required. Replace the image tag and keep the existing `/config` and `/data` mounts. Default local JSON logs are saved to `/data/logs/events.jsonl` (10 MiB per file, five backups) in addition to Docker logs. Ensure UID 10001 can write to `/data` as before.

To enable Loki without editing the mounted config, add this environment variable to the collector service in your TrueNAS app YAML:

```yaml
environment:
  KOTEL_LOKI_URL: http://loki.skritek.jankovice/loki/api/v1/push
  # Keep your existing secret environment variables here.
```

The URL must be reachable from the collector container; preserve your existing backend network attachment. No Docker logging plugin is needed. An omitted/empty URL disables Loki. Alternatively configure `logging.loki_url` in config.yaml; the environment variable takes precedence. Optional limits: `logging.queue_size` (2000), `logging.file_max_bytes` (10485760), `logging.file_backups` (5).

Loki uses a separate worker, five-second HTTP timeouts, batches up to 100, and retry backoff up to 60 seconds. Only HTTP 204 confirms delivery. Permanent 4xx rejections (except 408/429) drop the rejected batch so later events can proceed. Full queues drop new remote entries; local logging continues. The remote queue is in memory: pending logs are lost on restart, and rotated local files are **not automatically replayed**. Local retention is size-based, not a guaranteed number of days. Delivery failures appear in local/Docker logs and queue/drop counters in `kotel-reader status`. HTTP bodies from Loki are never logged.

Events include startup/shutdown, login attempts, session/MQTT/availability changes, retry deadlines, recovery, and a summary every minute. `unchanged_response_s` reports an unchanged payload, not proven stale measurements. Invalid responses include validation reason, item index/field types when applicable, body length and SHA-256; arbitrary response values, keys, login HTML, cookies, credentials and tokens are not copied into logs. Validation rules and authentication retry policies are unchanged.

In Grafana Explore, select Loki:

```logql
{app="kotel-logger", device_id="2985"} | json
```

Filter an incident with `| event="http_error"`. Use the time range selector for the incident; `details.run_id` correlates events from one process run. Loki transport follows the [official push API](https://grafana.com/docs/loki/latest/reference/loki-http-api/#ingest-logs).

HTTP 429/5xx: Retry-After is recorded as parsed seconds and respected by the scheduler. Error body SHA-256, fixed HTML markers and server-family classification are logged; arbitrary HTML/header values are excluded.
