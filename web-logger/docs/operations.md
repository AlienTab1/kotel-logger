> Version 0.3.0 recovery policy and upgrade details: [recovery-0.3.md](recovery-0.3.md). This supersedes historical retry descriptions below.

# kotel-reader 0.2 operations guide

[← Project introduction](../README.md) · [TrueNAS Apps](truenas-apps.md) · [GitHub](github.md)

One Python container reads Thematronic every 15 seconds. Prometheus scrapes
`/metrics` and stores history; Grafana queries Prometheus. The collector also
publishes MQTT Discovery and current values for Home Assistant. PostgreSQL and SQL migrations are not used.

## Configuration

Run the following commands from `web-logger`. For TrueNAS UI management, use
the [dedicated guide](truenas-apps.md).

```sh
cp config/config.example.yaml config/config.yaml
cp .env.example .env
mkdir -p secrets
```

- `config/config.yaml`: Thematronic, polling/session settings, `telemetry.stale_s`, Prometheus HTTP listener and MQTT.
- `config/signals.yaml`: types, English labels, units, `prometheus: true` for metrics and `publish: true` for HA.
- `.env`: dataset path, actual NAS LAN IP (`KOTEL_METRICS_BIND_IP`), available published port (`KOTEL_METRICS_PORT`) and secret paths.
- `secrets/thematronic_username`, `secrets/thematronic_password`, `secrets/mqtt_password`: one value per file.

The default internal port is 9108. If you change `prometheus.port`, also change
the Compose `target`; the host port can change independently. `0.0.0.0` listens
inside the container; the host LAN IP belongs in the port mapping.

Do not commit secrets or personal configuration. `*_FILE` settings take precedence
over `THEMATRONIC_USERNAME`, `THEMATRONIC_PASSWORD` and `MQTT_PASSWORD` environment
variables. Direct environment values are suitable for local development. Set
`mqtt.enabled: false` to disable MQTT; an empty MQTT password file must still exist for the Compose mount.

UID/GID `10001:10001` needs read access to configuration/secrets, traversal of
parent directories and write access to `/data`. File secrets retain host permissions;
Compose does not automatically encrypt them or necessarily change their owner.
YAML changes require a restart.

```sh
docker compose config --quiet
docker compose build
docker compose run --rm collector validate
docker compose up -d collector
docker compose logs --tail=100 -f collector
```

`validate` checks configuration, not passwords or connectivity. The image runs
as non-root with a read-only root filesystem, log rotation (3 × 10 MB), and
`unless-stopped` restart policy. Pause/logout does not make the healthcheck unhealthy;
it monitors the main loop and workers. An occupied metrics port causes visible
startup failure. Docker does not automatically restart a container merely because it is unhealthy.

## Prometheus and Grafana

Add the job from [prometheus-scrape.yaml](../deploy/prometheus-scrape.yaml) to the
existing `scrape_configs` list. Do not duplicate the key or overwrite other jobs.
The target is the actual NAS LAN IP and published port, without `http://`:

```yaml
scrape_configs:
  - job_name: kotel-reader
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    static_configs:
      - targets: ['REPLACE_NAS_LAN_IP:9108']
```

Run `promtool check config` and reload/restart according to your deployment.
The target should be UP. Prometheus connects to the collector; the collector does
not send history to Prometheus. The endpoint has no authentication or TLS: restrict
access to trusted LAN/monitoring clients. It is not an administration interface.
Scraping reads an in-memory sample and never calls Thematronic.

| Metric | Meaning |
|---|---|
| `kotel_temperature_celsius{device_id,signal}` | Valid current mapped temperatures |
| `kotel_maximum_temperature_celsius{device_id,signal}` | Mapped `max_t_*`, maximum over an unknown period |
| `kotel_signal_value{device_id,signal}` | Other mapped numbers |
| `kotel_signal_state{device_id,signal}` | Raw boolean 0/1, without inferred alarm meaning |
| `kotel_duration_seconds{device_id,signal}` | Duration gauge in seconds, not a timestamp |
| `kotel_signal_available{device_id,signal}` | Validity of each exported signal |
| `kotel_telemetry_available{device_id}` | Sufficiently recent valid web reception |
| `kotel_last_received_timestamp_seconds{device_id}` | Last reception time in UTC, not measurement time |
| `kotel_receive_age_seconds{device_id}` | Monotonic age of the last reception |
| `kotel_collector_healthy{device_id}` | Main-loop and worker health |
| `kotel_mqtt_connected{device_id}` | MQTT connection state |
| `kotel_session_state{device_id,state}` | Session state as a one-hot gauge |
| `kotel_reads_total`, `kotel_login_attempts_total`, `kotel_http_errors_total`, `kotel_session_losses_total` | Operational counters |
| `kotel_build_info{device_id,version}` | Collector version |

Prometheus adds `job` and `instance`. `up` indicates scrape success, not freshness
of boiler measurements. Unknown signals and text are not exported as metrics or
dynamic labels. Add explicit mappings after verifying their type and meaning.

Pause/logout removes measurement values as soon as the state is processed.
Network failures remove values no later than `telemetry.stale_s` after the last
reception. Availability is recomputed on every scrape, even without new samples.
A missing sensor, vendor `nepripojeno` state or invalid placeholder must never become a valid zero.

Import the [dashboard](../deploy/grafana-dashboard.json) and select your existing
Prometheus datasource. Optional [provisioning](../deploy/grafana-datasource.yaml)
uses `KOTEL_PROMETHEUS_URL` in Grafana, e.g. `http://PROMETHEUS_IP:9090`.
Variables `job`, `instance` and `device_id` distinguish sources. Times follow the
browser timezone; Europe/Prague can be selected. Nulls are not connected, although
wide time ranges may hide short gaps through reduced resolution. Structured stdout
events can be forwarded by your existing log collector; direct Loki delivery is optional in 0.2.2.

## History and outages

Prometheus manages history and retention. **There is no backfill during Prometheus
outages.** The collector continues reading and publishing MQTT but does not archive
intermediate samples. After recovery, scraping obtains only the current valid sample.
Stopping the collector also stops reads. Scrape intervals need not capture every HTTP sample.

`/data/state.sqlite` stores pause, cooldown, diagnostics, counters and one latest
snapshot. It is not a historical queue. The latest raw response remains available
for diagnostics but is never restored as current telemetry after restart. Successful
HTTP reception does not establish source freshness; the known JSON has no measurement timestamp.

## MQTT and Home Assistant

Broker, TLS and prefix are configurable. The default prefix is
`kotelna/thematronic/2985`; HA must use the same MQTT broker.

- `…/state`: current JSON, non-retained, QoS 1, no historical replay.
- `…/process`: retained process/MQTT state, LWT `offline`.
- `…/availability`: retained telemetry availability.
- `homeassistant/.../config`: retained Discovery with stable IDs.
- `homeassistant/status=online`: republish Discovery and only a still-valid sample.

Sensor availability requires process availability, telemetry availability and
validity of that individual value. Sensors also expire according to the shared
`telemetry.stale_s`. Exporter and MQTT use the same data, so validity changes apply
to both. QoS 1 permits duplicates. Ambiguous booleans have neutral descriptions;
`s_o_hav_term=true` is not a confirmed emergency.

The [HA dashboard](../deploy/ha-kotelna.yaml) requires no HACS. When changing
DeviceID, update the prefix and dashboard entities as well. Remove obsolete Discovery
configuration by sending an empty retained message to its specific `.../config` topic.

## Sessions and local commands

Default `read_only` does not call `DeviceSignalReload`. Optional `reload_then_read`
uses `reload_wait_s`; its two-second default is not live-verified, and a 204 response
does not confirm a new measurement. Boiler-control endpoints are not implemented.

Without a known session lifetime, every confirmed logout uses session.cooldown_s (default 60 seconds since 0.3.0; existing explicit 300-second settings are preserved)
of cooldown. With `expected_session_lifetime_s` configured, a 30-second cooldown is
allowed only if the entire interval from last success to first rejection lies within
expected expiry ±60 seconds. Losing a new session within 120 seconds always triggers
the long cooldown. Automatic lifetime estimation is not implemented; manually
calibrate using at least three consistent uninterrupted sessions. Cookie expiry is
not proof of server-session expiry.

Failed logins use 5/10/20/40/60-minute backoff with small jitter. HTTP 429 respects
Retry-After. Read-time 5xx, timeout or malformed JSON does not cause another login.
Repeated ambiguous 403 responses trigger a long cooldown and diagnostics. Recognized
invalid credentials enter AUTH_FAILED until corrected and restarted; actual error
texts still require live verification.

```sh
docker compose exec collector kotel-reader status
docker compose exec collector kotel-reader pause --minutes 30
docker compose exec collector kotel-reader resume
docker compose exec -T collector kotel-reader snapshot > latest-snapshot.json
```

Commands use the local state database. Pause takes effect after the running HTTP
request and also cancels pending login. Resume does not override server backoff.
Run one instance per account: the file lock protects a shared `/data`, not separate hosts/directories.

## Login failure diagnostics

Check logs and state of the running container; adjust its name for your Apps deployment:

```sh
docker logs --tail 50 ix-kotel-logger-collector-1
docker exec ix-kotel-logger-collector-1 kotel-reader status
```

Since 0.2.1, error logs and `status.last_http_error` contain sanitized diagnostics:
`stage` (`login_form`, `login_submit`, `device_signal`), method, optional HTTP status,
compression type and `exception_type`. Passwords, tokens, cookies, response bodies
and URL query parameters are excluded. The last error survives backoff/restart and
is cleared after a valid sample.

`network_error` does not mean a wrong password. Use `exception_type` to distinguish
timeout, connection and protocol errors. `response_decode_error` means response
decoding failed; `login_form_not_found` means form recognition failed;
`login_not_confirmed` means the session was not verified. Only `invalid_credentials`
represents a recognized invalid-credentials response.

Version 0.2.0 could preserve the compression header after HTTPX decompression,
causing a newly constructed response to decompress the body again. This could log
`GET ... 200 OK`, followed by `network_error` before form submission. Version 0.2.1
fixes it; regression tests cover compressed forms, redirects, cookies and compressed DeviceSignal responses.

Update through Apps: Commit/Push → **Publish container image** → wait for success
→ **Edit YAML** → set the new specific `sha-...` tag. This fix does not require
changing secrets. Persisted backoff remains active, so the updated collector may
wait until `next_attempt`. Do not delete `/data` merely to force another login.
The next attempt and its details appear in logs/status.

## Upgrading from 0.1, backups and rollback

1. Stop the old collector and back up `/data` and any PostgreSQL history.
2. Start from the new YAML. Remove `postgres` and `queue`, move `mqtt.stale_s`
   to `telemetry.stale_s`, and add `prometheus.host` and `prometheus.port`.
3. Remove the PostgreSQL secret from deployment and add the scrape port mapping.
4. Replace the Grafana SQL dashboard with the Prometheus dashboard and configure scraping.
5. Startup migrates cooldown/pause deadlines and counters once from `queue.sqlite`
   to `state.sqlite`. The old file and PostgreSQL are not changed or deleted.
   The historical queue is no longer delivered. If it must be preserved, export it
   with the old version or let the old collector deliver it before upgrading.

New snapshots are not added to the old file. Archive the old queue as needed.
Back up SQLite with the collector stopped, including any WAL files; do not copy
only a live main database. Back up Prometheus data/configuration separately.
Protect secrets along with configuration. Rollback to 0.1 also requires its
compatible configuration and stored operational state. Never run two instances for one account during migration.

## Tests and live commissioning checklist

`python -m pytest -q` uses mock HTTP, a fake clock and a real loopback HTTP endpoint.
Tests block external network access. `compose.test.yaml` runs isolated Prometheus,
an MQTT broker and a synthetic exporter without boiler credentials, and verifies
PromQL expressions for all panels.

```sh
docker build -t kotel-reader:0.2.1 .
docker compose -p kotel-reader-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kotel-reader-test -f compose.test.yaml down -v
```

- [ ] Dataset, ACLs for 10001, available LAN IP/port and broker reachability.
- [ ] GitHub Actions successfully built and tested the container.
- [ ] Prometheus target is UP; `kotel_telemetry_available` matches actual reception.
- [ ] Pause/logout removes temperature series and makes HA sensors unavailable.
- [ ] Restart does not restore the old snapshot as a new sample.
- [ ] Verify Reload, actual session lifetime and authentication errors with a real account.
- [ ] Verify signal meaning, boolean polarity and dashboard readability.
- [ ] Verify recovery after MQTT/Prometheus outages, backup and rollback.

Live login and TrueNAS deployment were not performed during development.
Thematronic automation is not confirmed as officially supported; the collector does not bypass limits.

## Extended temperature/state data

Both supplied dumps have the same 130 signal names. The collector already reads
the entire DeviceSignal array; extension changes mappings, not the HTTP request
count. Matching against the live collector snapshot still needs verification.
Zone 2 room temperature uses `dev_prost_z2` (21 °C in the dump), not disconnected
`s_t_zona2`. Water targets are `s_t_zo1_voda_poz` and `s_t_zo2_voda_poz`; other
similarly named demands are not merged.

Maxima are exported separately; their periods and reset behavior are unverified.
Booleans and mode codes remain raw values requiring interpretation, without alarm
device classes. Text modes use MQTT only. Reserved I/O and signals with unverified
units remain in the diagnostic snapshot. DeviceSignalReload timing and session
lifetime still require commissioning verification.

When updating, back up and merge `config/signals.yaml` with the NAS-mounted
configuration, retaining your customizations, then restart. A new image does not
overwrite bind-mounted configuration. Version 0.2.1 already supports these mappings.
Reimport `deploy/grafana-dashboard.json` for new panels. The deployed Prometheus
job is `kotel-logger`; adjust the dashboard job selector if using the example `kotel-reader` name.

## Version 0.2.2: outage diagnostics and Loki

Build through GitHub Actions, update the TrueNAS image tag and optionally add this
to the existing environment:

```yaml
KOTEL_LOKI_URL: http://loki.skritek.jankovice/loki/api/v1/push
```

Keep existing networking, secrets and `/data`/`/config` mounts. Configuration 0.2.x
and `state.sqlite` are unchanged; no migration or replacement of `signals.yaml`
is required. Without a URL, Loki is disabled. No backend Docker network name is
filled in automatically; Loki must actually be reachable from the container.

Local log: `/data/logs/events.jsonl`, rotating at 10 MiB with five backups. Docker
logging remains enabled. Loki uses a separate thread, bounded in-memory queue,
batches up to 100 and retry delays up to 60 seconds. Loki outages do not block
HTTP collection. The queue is not persistent: restart/overflow can lose remote
entries, and local files are not automatically backfilled. Delivery state, errors
and dropped-record counts appear in `kotel-reader status`. `loki_delivery_failed`
events stay in local/Docker logs to avoid recursive delivery.

Every minute emits `collector_summary`; availability/MQTT/session changes emit
`state_changed`, and successful recovery emits `read_recovered`. `http_error`
includes `validation_reason`, JSON type, item count, optional bad-item index/field
types, response length/fingerprint, request time and next attempt time. Response
bodies and arbitrary server text are not logged. `unchanged_response_s` does not
prove stale source data: idle readings may legitimately remain identical.

Grafana Loki: `{app="kotel-logger", device_id="2985"} | json`.
Log timestamps are UTC; Prague was UTC+2 on 18 October 2026.

The supplied 18 October incident attachment covers 15:02–18:28 Czech local time.
It contains timeouts, HTTP 503 at 17:03, session loss at 17:08, confirmed login at
17:13, then HTTP 200 / invalid_payload during 17:15–18:28. It does not establish
the full six-hour outage or the later 19:43 recovery, which are outside the supplied
file. HTTP 503 establishes a remote service/proxy error; timeouts alone cannot
distinguish the NAS network from Thematronic. Those logs do not establish the
specific reason for rejecting the JSON.

For HTTP 429/5xx, Retry-After is logged as seconds and respected for the next attempt.
HTML errors retain a fingerprint, recognized fixed markers and server family;
arbitrary HTML and header values are not copied.
