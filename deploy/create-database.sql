-- Spusťte jako administrátor PostgreSQL, mimo transakci, pouze při prvním založení.
-- Hesla nastavte interaktivně přes psql \password; nepřidávejte je do tohoto souboru.
CREATE ROLE kotel_migrator LOGIN;
CREATE ROLE kotel_writer LOGIN;
CREATE ROLE kotel_grafana LOGIN;
CREATE DATABASE kotelna OWNER kotel_migrator;
REVOKE ALL ON DATABASE kotelna FROM PUBLIC;
GRANT CONNECT ON DATABASE kotelna TO kotel_migrator, kotel_writer, kotel_grafana;

