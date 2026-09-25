CREATE SCHEMA IF NOT EXISTS kotel;
CREATE TABLE kotel.snapshots (
    sample_id uuid PRIMARY KEY,
    device_id bigint NOT NULL,
    received_at timestamptz NOT NULL,
    source_timestamp timestamptz,
    raw jsonb NOT NULL,
    "values" jsonb NOT NULL,
    statuses jsonb NOT NULL
);
CREATE INDEX snapshots_device_received ON kotel.snapshots(device_id, received_at);
CREATE INDEX snapshots_retention ON kotel.snapshots(received_at);
CREATE TABLE kotel.session_events (
    event_id uuid PRIMARY KEY,
    device_id bigint NOT NULL,
    occurred_at timestamptz NOT NULL,
    event text NOT NULL,
    details jsonb NOT NULL
);
CREATE INDEX events_device_time ON kotel.session_events(device_id, occurred_at);
CREATE INDEX events_retention ON kotel.session_events(occurred_at);

CREATE VIEW kotel.temperatures AS
SELECT s.device_id, s.received_at, s.source_timestamp, v.key AS signal,
       CASE WHEN jsonb_typeof(v.value) = 'number'
            THEN (v.value #>> '{}')::double precision END AS temperature
FROM kotel.snapshots s CROSS JOIN LATERAL jsonb_each(s."values") v
WHERE v.key IN ('s_t_spal','s_t_ko_vy','s_t_ko_vr','s_t_akuh','s_t_akus','s_t_akud',
                's_t_venku','s_t_zo1_vy','s_t_zo2_vy','s_t_tuv');

CREATE VIEW kotel.binary_states AS
SELECT s.device_id, s.received_at, v.key AS signal,
       CASE WHEN jsonb_typeof(v.value) = 'boolean' THEN (v.value #>> '{}')::boolean END AS state
FROM kotel.snapshots s CROSS JOIN LATERAL jsonb_each(s."values") v
WHERE v.key IN ('s_o_hav_term','s_o_ko_cerp','s_o_ko_vent');

