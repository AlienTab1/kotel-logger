"""Regenerate the committed importable Grafana dashboard, without network access."""
import json
from pathlib import Path

DS = {"type": "postgres", "uid": "${DS_KOTELNA}"}
panels = []


def panel(title, kind, sql, x, y, w=12, h=8, unit=None):
    data = {
        "id": len(panels) + 1, "title": title, "type": kind,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": DS, "rawQuery": True, "rawSql": sql,
                     "format": "table" if kind in ("table", "stat") else "time_series"}],
        "fieldConfig": {"defaults": {"custom": {"spanNulls": False, "insertNulls": 60000}}, "overrides": []},
        "options": {},
    }
    if unit:
        data["fieldConfig"]["defaults"]["unit"] = unit
    if kind == "stat":
        data["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}
    panels.append(data)


def temperatures(names):
    selected = ",".join("'" + name + "'" for name in names)
    # Add explicit null buckets for absent reads. $__interval_ms aggregates long time ranges.
    return f"""WITH parameters AS (
  SELECT greatest(15, ($__interval_ms / 1000)::integer) AS step
), buckets AS (
  SELECT generate_series(
    floor($__unixEpochFrom()::numeric / step)::bigint * step,
    floor($__unixEpochTo()::numeric / step)::bigint * step, step) AS bucket FROM parameters
), names AS (SELECT unnest(ARRAY[{selected}]) AS signal), readings AS (
  SELECT floor(extract(epoch FROM received_at) / step)::bigint * step AS bucket,
         signal, avg(temperature) AS value
  FROM kotel.temperatures CROSS JOIN parameters
  WHERE device_id = ${{device_id:sqlstring}}::bigint AND $__timeFilter(received_at)
    AND signal IN ({selected})
  GROUP BY 1,2
)
SELECT to_timestamp(b.bucket) AS time, n.signal AS metric, r.value
FROM buckets b CROSS JOIN names n LEFT JOIN readings r USING(bucket,signal)
ORDER BY 1,2"""


panel("Kotel a spaliny", "timeseries", temperatures(["s_t_spal", "s_t_ko_vy", "s_t_ko_vr"]), 0, 0, unit="celsius")
panel("Akumulace", "timeseries", temperatures(["s_t_akuh", "s_t_akus", "s_t_akud"]), 12, 0, unit="celsius")
panel("Zóny a venek", "timeseries", temperatures(["s_t_zo1_vy", "s_t_zo2_vy", "s_t_venku"]), 0, 8, unit="celsius")
panel("Poslední příjem ve vybraném období", "stat", "SELECT max(received_at) AS received_at FROM kotel.snapshots WHERE device_id = ${device_id:sqlstring}::bigint AND $__timeFilter(received_at)", 12, 8, h=4, unit="dateTimeAsIso")
panel("Počet vzorků ve vybraném období", "stat", "SELECT count(*) AS samples FROM kotel.snapshots WHERE device_id = ${device_id:sqlstring}::bigint AND $__timeFilter(received_at)", 12, 12, h=4)
panel("Raw booleany — význam a polarita k ověření", "state-timeline", """WITH parameters AS (
 SELECT greatest(15, ($__interval_ms / 1000)::integer) AS step
), buckets AS (
 SELECT generate_series(floor($__unixEpochFrom()::numeric/step)::bigint*step,
  floor($__unixEpochTo()::numeric/step)::bigint*step,step) AS bucket FROM parameters
), names AS (SELECT unnest(ARRAY['s_o_hav_term','s_o_ko_cerp','s_o_ko_vent']) AS signal), readings AS (
 SELECT floor(extract(epoch FROM received_at)/step)::bigint*step AS bucket, signal,
   CASE WHEN bool_and(state) THEN 'true' WHEN NOT bool_or(state) THEN 'false'
        WHEN count(state)>0 THEN 'mixed' END AS value
 FROM kotel.binary_states CROSS JOIN parameters
 WHERE device_id=${device_id:sqlstring}::bigint AND $__timeFilter(received_at) GROUP BY 1,2
)
SELECT to_timestamp(b.bucket) AS time,n.signal AS metric,r.value
FROM buckets b CROSS JOIN names n LEFT JOIN readings r USING(bucket,signal) ORDER BY 1,2""", 0, 16, w=24)
panel("Mezery mezi příjmy > 60 s", "table", """WITH samples AS (
 SELECT received_at,lag(received_at) OVER (ORDER BY received_at) AS previous
 FROM kotel.snapshots WHERE device_id=${device_id:sqlstring}::bigint AND $__timeFilter(received_at)
)
SELECT previous AS gap_start,received_at AS gap_end,extract(epoch FROM received_at-previous) AS seconds
FROM samples WHERE received_at-previous>interval '60 seconds' ORDER BY received_at DESC LIMIT 200""", 0, 24)
panel("Události relací", "table", "SELECT occurred_at,event,details::text FROM kotel.session_events WHERE device_id=${device_id:sqlstring}::bigint AND $__timeFilter(occurred_at) ORDER BY occurred_at DESC LIMIT 200", 12, 24)

dashboard = {
    "uid": "kotelna-thematronic", "title": "Kotelna — Thematronic", "schemaVersion": 39,
    "version": 1, "timezone": "browser", "refresh": "30s", "tags": ["kotelna"],
    "description": "Časy příjmu v UTC; čas a čerstvost měření regulátoru nejsou ověřeny. Mezery se nespojují. Dlouhé rozsahy agregují údaje do časových bucketů.",
    "time": {"from": "now-6h", "to": "now"},
    "__inputs": [{"name": "DS_KOTELNA", "label": "Kotelna PostgreSQL", "type": "datasource", "pluginId": "postgres", "pluginName": "PostgreSQL"}],
    "templating": {"list": [{"name": "device_id", "label": "Zařízení", "type": "query", "datasource": DS,
        "query": "SELECT DISTINCT device_id FROM kotel.snapshots ORDER BY 1", "refresh": 1,
        "current": {"text": "2985", "value": "2985"}, "multi": False, "includeAll": False}]},
    "panels": panels,
}
Path("deploy/grafana-dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

