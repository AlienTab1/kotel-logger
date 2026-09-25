-- Spusťte po migraci v DB kotelna jako vlastník/migrátor nebo administrátor.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA kotel TO kotel_writer, kotel_grafana;
GRANT SELECT, INSERT, DELETE ON kotel.snapshots, kotel.session_events TO kotel_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA kotel TO kotel_grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE kotel_migrator IN SCHEMA kotel
  GRANT SELECT ON TABLES TO kotel_grafana;
ALTER ROLE kotel_grafana SET default_transaction_read_only = on;

