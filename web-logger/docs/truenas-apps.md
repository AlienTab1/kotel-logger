# TrueNAS Apps → Install via YAML (Prometheus + MQTT)

[← Introduction](../README.md) · [Operations and metrics](operations.md)

For TrueNAS with Docker Apps (24.10+). The collector does not need PostgreSQL or
a migration app. GitHub builds the image; TrueNAS pulls and manages it through Apps.

## 1. Prepare the image on GitHub

1. Commit and push all changes using TortoiseGit, including removal of old paths.
2. On GitHub, select **Actions → Publish container image → Run workflow**.
3. Wait for successful tests and build. Copy the image address from the summary:
   `ghcr.io/alientab1/kotel-logger:sha-<12-character-commit-prefix>`.
4. For anonymous pulls, set **Packages → kotel-logger → Package settings → Public**
   if you intend to publish the image and its code. Secrets are not included in the image.
   For a private package, configure TrueNAS registry authentication to `ghcr.io`
   with a `read:packages` token using the settings appropriate to your version.

The workflow uses the automatic `GITHUB_TOKEN`, not boiler/MQTT credentials.
It runs manually and publishes a specific tag and `latest`. Deploy the specific
tag so that rollback is possible. A public repository does not automatically make its image public.

## 2. Prepare the dataset and configuration

In **Datasets**, choose your own pool and dataset. This path is a template:

```text
/mnt/REPLACE_POOL/kotel-logger/
  config/
    config.yaml
    signals.yaml
  data/
  secrets/
    thematronic_username
    thematronic_password
    mqtt_password
```

Copy [config.example.yaml](../config/config.example.yaml) to the host as
`config/config.yaml`, along with [signals.yaml](../config/signals.yaml).
Existing SFTP/SMB access is sufficient; cloning the repository on the NAS is optional.

Set the MQTT broker IP/hostname, username and TLS options in `config.yaml`.
Keep `data_dir: /data`, `signals_file: signals.yaml`, `read_only`,
`expected_session_lifetime_s: null` and `telemetry.stale_s: 60`.
`prometheus.host: 0.0.0.0` and `port: 9108` configure the listener inside the container.

Each of the three secret files must contain only its value, without quotation marks.
ACLs must let UID/GID `10001:10001` read configuration/secrets and write `data`;
parent directories must allow traversal. Do not expose secrets to other share users.

## 3. Import the app

1. Select **Apps → Discover Apps → ⋮ → Install via YAML**.
2. Use app name `kotel-logger`.
3. Paste [truenas-app.yaml](../deploy/truenas-app.yaml).
4. Replace `image` with the specific Actions image address and replace **all**
   `/mnt/REPLACE_POOL/...` paths with actual paths.
5. Replace `host_ip: REPLACE_NAS_LAN_IP` with the NAS LAN IP.
   Check that host port `9108` is free; otherwise change `published`.
   The internal `target: 9108` must match `prometheus.port`.
6. Save and wait for the image pull and startup.

Use this standalone YAML: it requires no `build: .`, relative files or local `.env`.
The `web-logger/compose.yaml` file is the CLI-managed alternative. Bridge networking
is sufficient. The collector needs outbound HTTPS and MQTT; Prometheus needs access
to the NAS metrics port. The endpoint has no authentication/TLS and is intended for
a trusted network, not Internet exposure. A dedicated macvlan IP is unnecessary.

## 4. Connect the existing Prometheus

Add the job from [prometheus-scrape.yaml](../deploy/prometheus-scrape.yaml) to your
Prometheus configuration. Set the target to the NAS LAN IP and published port.
Preserve existing jobs:

```yaml
scrape_configs:
  - job_name: kotel-reader
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    static_configs:
      - targets: ['REPLACE_NAS_LAN_IP:9108']
```

Validate and reload/restart Prometheus according to your deployment. The target
must be UP in **Targets**. `kotel_temperature_celsius` returns valid temperatures
after a successful read. While Prometheus is down, the collector can still read
and publish MQTT, but the history gap will not be backfilled.

## 5. Grafana and Home Assistant

Import the [dashboard JSON](../deploy/grafana-dashboard.json) into Grafana and
select the existing **Prometheus datasource**. Optional
[provisioning](../deploy/grafana-datasource.yaml) uses the Grafana environment
variable `KOTEL_PROMETHEUS_URL`.

In HA, configure MQTT integration with the same broker. Discovery creates sensors;
display them on the [Boiler room dashboard](../deploy/ha-kotelna.yaml).
Availability requires a valid recent reception.

## Verification and updates

Open logs and a shell for the `collector` container in Apps:

```sh
kotel-reader status
kotel-reader snapshot
kotel-reader pause --minutes 30
kotel-reader resume
```

`snapshot` is the latest diagnostic sample and may be old. After 60 s without a
valid new reception, during a pause or after logout, temperature series disappear
from `/metrics`. Restarting does not reuse the stored snapshot as current telemetry.

Update: Commit/Push → manually run **Publish container image** → **Edit YAML**
in Apps → set the new image tag. Editing a GitHub file does not update an already
running app. When upgrading from 0.1, use the new configuration, remove the PostgreSQL
secret and retire the old migration app if present. Existing databases and queues
are not automatically deleted; see the [upgrade procedure](operations.md).

For `permission denied`, check ACLs/UID 10001. For pull failures, check the tag and
GHCR authentication/visibility. For an unreachable target, check port, NAS LAN IP
and connectivity between Prometheus and the NAS. Live NAS deployment and Thematronic
behavior still require commissioning verification.

Sources: [TrueNAS Custom Apps](https://apps.truenas.com/managing-apps/installing-custom-apps/),
[image publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images),
[GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
