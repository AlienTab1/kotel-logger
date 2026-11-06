# Web collector recovery, version 0.3.0

## November 6 incident: what is and is not established

The supplied evidence reports `invalid_or_duplicate_name`, item index 1, in a 130-item JSON response during login verification. The old validator combined empty names, non-string names and duplicates into one exception. No rejected response was supplied, and the old `snapshot` only retained the last **accepted** response. Therefore the precise offending identifier cannot be recovered from this evidence. Do not infer that index 1 had the same identifier as an earlier capture.

Two implementation causes are confirmed: one bad optional item rejected the entire batch; any exception inside login verification used authentication backoff up to one hour. HTTP 200 alone proves neither authentication nor usable measurements.

The new validator separates name-empty, wrong-type, malformed and duplicate categories. It checks every item, retains accepted item order and identifiers without renaming, and quarantines every occurrence of a duplicated identifier. Identical duplicates are also quarantined. Diagnostics preserve original indices, conflicting indices (first 100 plus total), fixed field names/types, and exact names only for configured signals. Unknown or malformed identifiers are hashed, not copied into logs. Up to 50 issues are included with an explicit truncation flag; rejected values are never logged. HTTP diagnostics include response length and SHA-256. The local `diagnostics` command reports the last validation attempt independently of the last good `snapshot`.

Only a nonempty JSON array can be accepted. Partial arrays of any length are allowed, with no special 12/18/130-item assumption. Nested objects as measurements, invalid field types, nonfinite numbers and malformed JSON remain invalid. At least one mapped signal must normalize successfully. Optional missing, malformed, disconnected or conflicting sensors remain unavailable. Set `required: true` on selected entries in `signals.yaml` to reject the entire sample if those signals are missing or unusable. No signals become required automatically. A complete valid JSON array is distinguished from truncated/invalid JSON, which is never repaired.

## Retry and session policy

| Category | Before | 0.3.0 defaults |
|---|---|---|
| Network / HTTP 5xx while reading | Poll interval doubled, up to 300 s | 10, 20, 40, 60 s |
| Payload validation while reading | Same read backoff | 15, 30, 60 s |
| Network / invalid payload during login | Authentication backoff, 300–3600 s | Transient/validation backoff; retry verification with existing cookies after a successful submission |
| Confirmed session loss | Usually 300 s | Configurable 60 s; existing explicit 300 stays 300 |
| Recognized expected expiry | Optional 30 s | Preserved |
| Authentication rejection | 300, 600, 1200, 2400, 3600 s | Preserved, configurable auth_initial_s/auth_max_s |
| Explicit invalid credentials | AUTH_FAILED, stop automatic login | Preserved; fix credentials and restart |
| HTTP 429 / server Retry-After | Honored | Honored; never shortened by manual retry |

Transient jitter is positive, 0–10%, capped at max_s; base delays are not exact wall-clock intervals because requests take time. Request timeouts and scheduling are separate. New default connect/read timeouts are 5/10 seconds. Existing explicit timeout_s applies to both unless overridden. Login submissions and validation failures have separate accounting; HTTP 401 invalidates cookies and active telemetry. Repeated rejection during unverified authentication follows conservative authentication backoff. Failures reset only after validated usable data. HTTP 200 with unusable data never marks the session authenticated.

New settings (merge into existing sections, do not duplicate YAML keys):

```yaml
thematronic:
  connect_timeout_s: 5
  read_timeout_s: 10
session:
  cooldown_s: 60
  auth_initial_s: 300
  auth_max_s: 3600
retry:
  transient_initial_s: 10
  validation_initial_s: 15
  max_s: 60
  jitter: 0.1
```

Persisted deadlines use monotonic time while running. A backwards wall-clock jump at restart cannot extend the wait beyond the originally recorded duration. Ordinary transient deadlines are bounded by retry.max_s after restart; explicitly server-directed and authentication waits are preserved. An old login_retry paired with a recorded invalid_payload is recognized as a validation wait. Legacy state with insufficient evidence remains conservative.

## Health, commands and monitoring

Docker's existing healthcheck checks **liveness**; do not change it to readiness or restart the container whenever Thematronic is down. `/metrics` runs independently of upstream requests. Existing `kotel_telemetry_available`, `kotel_receive_age_seconds`, `kotel_last_received_timestamp_seconds`, and `kotel_signal_available` report receipt freshness and sensor availability. Stale measurement series disappear, never become zero. A restart never restores a historical sample as live telemetry.

`status` adds reception_health (fresh/stale/unavailable), seconds_since_last_read, consecutive failures, retry reason/deadline, category totals and observed outage seconds. Fresh means recently received, **not** independently verified fresh at the boiler. Paused or explicitly invalidated historical data is marked stale. Source freshness and DeviceSignalReload timing remain unverified.

New metrics: `kotel_consecutive_http_failures`, `kotel_consecutive_validation_failures`, `kotel_retry_remaining_seconds`, `kotel_next_attempt_timestamp_seconds`, `kotel_errors_by_category_total`, `kotel_recoveries_total`, `kotel_rejected_signals_total`, `kotel_upstream_outage_seconds_total`. Outage time measures running process time from first error to a usable response; excludes stopped-process time and can include a manual pause during an already active outage. It is not an estimate of source measurement age. Category labels are a fixed six-value set. Existing aggregate metrics remain compatible.

```bash
docker exec kotel-logger kotel-reader status
docker exec kotel-logger kotel-reader diagnostics
docker exec kotel-logger kotel-reader readiness
docker exec kotel-logger kotel-reader retry
```

`retry` is asynchronous; inspect retry_requested in logs for allowed=true/false. It never bypasses manual pause, AUTH_FAILED, protected authentication waits or server rate limits. `resume` only lifts manual pause and does not bypass cooldowns. `snapshot` retains its existing interface for the last accepted data; use `diagnostics` for rejected responses without raw values. During partial recovery snapshot.raw contains only accepted items, with original ordering; rejected-item details live in diagnostics.

Example alert rules are in [prometheus-alerts.yaml](../deploy/prometheus-alerts.yaml). Add them to your existing Prometheus rule_files. They include exporter-down cases separately from live-but-unavailable telemetry. Change job if your deployed scrape job differs.

## Upgrade and rollback on Skřítek

1. Save the current pinned image tag and app/Compose YAML. Stop the collector briefly and back up its entire existing data directory (including SQLite WAL/SHM if present), plus config and secrets using your normal protected backup. Start it again if image build is not yet ready.
2. Commit/push the web-logger changes, run the existing **Publish container image** GitHub Action, and wait for tests/build to succeed. Use its immutable sha tag.
3. Change only the collector image tag, retain /data and /config mounts, secrets, Loki URL, ports and backend network, and recreate the container through the existing deployment method. Keep one instance only.
4. Verify version (`docker exec kotel-logger python -c 'from kotel_reader import __version__; print(__version__)'`), status, diagnostics and metrics. If upstream is unavailable, liveness may be healthy while readiness fails; verify retry deadlines are bounded rather than demanding immediate measurements.

No mandatory config changes. Optional: replace explicit cooldown_s: 300 with 60 and set connect_timeout_s/read_timeout_s to 5/10. If you want the easiest rollback, initially leave existing configuration unchanged. No schema migration: new diagnostics use additional keys in the existing state table. Sensor mappings, metric names, HA unique IDs and mounts remain compatible.

Rollback: stop the new collector, remove any **new** retry section, auth_initial_s/auth_max_s, split timeout keys from config before using an older binary (its config parser rejects unknown settings), and restore cooldown_s >=300 if rolling back to 0.2.x. Restore the previous image tag, keep the same volumes, and start exactly one collector. New required flags should also be removed when rolling back so their safety expectation is not silently lost. Pending retry commands should be drained before rollback; do not queue new commands while switching versions. Additional state keys are ignored by 0.2.x; its original schema is unchanged. Prefer retaining current state to preserve any server-directed wait. Only restore the backup for database damage, not to bypass cooldowns.

Tests use simulated HTTP responses and clocks. Disposable Docker tests exercise real MQTT and Prometheus in CI; never use the real Thematronic account for automated tests.
