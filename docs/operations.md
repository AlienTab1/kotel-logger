# Provozní příručka kotel-reader 0.2

[← Úvod projektu](../README.md) · [TrueNAS Apps](truenas-apps.md) · [GitHub](github.md)

Jeden Python kontejner čte Thematronic po 15 s. Prometheus si sám načítá `/metrics`
a ukládá historii, Grafana se dotazuje Promethea. Současně collector publikuje MQTT
Discovery a aktuální hodnoty pro Home Assistant. PostgreSQL ani SQL migrace se nepoužívají.

## Konfigurace

Příkazy níže jsou relativní vůči kořeni projektu. Pro správu aplikace přes TrueNAS UI
použijte [samostatný návod](truenas-apps.md).

```sh
cp config/config.example.yaml config/config.yaml
cp .env.example .env
mkdir -p secrets
```

- `config/config.yaml`: Thematronic, intervaly a relace, `telemetry.stale_s`, HTTP listener Promethea a MQTT.
- `config/signals.yaml`: typy, české popisky, jednotky, `prometheus: true` pro metriky a `publish: true` pro HA.
- `.env`: cesta datasetu, skutečná LAN IP NASu (`KOTEL_METRICS_BIND_IP`), volný publikovaný port (`KOTEL_METRICS_PORT`) a cesty k secrets.
- `secrets/thematronic_username`, `secrets/thematronic_password`, `secrets/mqtt_password`: každý soubor obsahuje jen jednu hodnotu.

Výchozí port uvnitř kontejneru je 9108. Pokud měníte vnitřní `prometheus.port`, změňte
i `target` v Compose; hostitelský port lze měnit samostatně. Listener `0.0.0.0` je
uvnitř kontejneru, hostitelská LAN IP se nastavuje v mapování portu.

Secrets a osobní konfiguraci necommitujte. Souborová varianta `*_FILE` má přednost
před `THEMATRONIC_USERNAME`, `THEMATRONIC_PASSWORD`, `MQTT_PASSWORD` v prostředí.
Pro lokální vývoj lze použít přímo tyto proměnné. MQTT lze vypnout pomocí
`mqtt.enabled: false`; prázdný soubor MQTT hesla musí i pak existovat pro Compose mount.

UID/GID `10001:10001` potřebuje čtení konfigurace a secrets, průchod potřebnými adresáři
a zápis do `/data`. Souborové secrets mají práva hostitele; Compose je automaticky
nezašifruje a nemusí změnit jejich vlastníka. Změny YAML vyžadují restart.

```sh
docker compose config --quiet
docker compose build
docker compose run --rm collector validate
docker compose up -d collector
docker compose logs --tail=100 -f collector
```

`validate` ověřuje konfiguraci, nikoli hesla nebo síť. Image běží jako non-root,
root filesystem je read-only, logy rotují (3 × 10 MB), restart je `unless-stopped`.
Pauza či odhlášení nejsou důvodem pro nezdravý healthcheck; ten sleduje hlavní smyčku
a workery. Při obsazeném metrics portu start selže viditelně. Docker samotný kvůli
stavu `unhealthy` automaticky kontejner nerestartuje.

## Prometheus a Grafana

Do existujícího seznamu `scrape_configs` v Prometheu přidejte job z
[prometheus-scrape.yaml](../deploy/prometheus-scrape.yaml). Nedublujte klíč
`scrape_configs` a nepřepisujte ostatní joby. `targets` je skutečná LAN IP NASu a
publikovaný port, bez `http://`:

```yaml
scrape_configs:
  - job_name: kotel-reader
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    static_configs:
      - targets: ['DOPLNTE_LAN_IP_NASU:9108']
```

Proveďte validaci `promtool check config` a reload/restart podle způsobu provozu
svého Promethea. V Targets má být `kotel-reader` UP. Prometheus se připojuje ke
collectoru, collector nikdy neodesílá historii do Promethea. Endpoint nemá autentizaci
ani TLS; přístup omezte na důvěryhodnou LAN/monitoring. Nejde o administrační rozhraní.
Každý scrape pouze přečte paměťový vzorek, nevolá Thematronic.

| Metrika | Význam |
|---|---|
| `kotel_temperature_celsius{device_id,signal}` | Platné aktuální namapované teploty |
| `kotel_maximum_temperature_celsius{device_id,signal}` | Namapované `max_t_*`, maximum neznámého období |
| `kotel_signal_value{device_id,signal}` | Ostatní namapovaná čísla |
| `kotel_signal_state{device_id,signal}` | Raw boolean jako 0/1; bez odhadu významu alarmu |
| `kotel_duration_seconds{device_id,signal}` | Doba jako gauge v sekundách, ne timestamp |
| `kotel_signal_available{device_id,signal}` | Platnost každého exportovaného signálu |
| `kotel_telemetry_available{device_id}` | Dostatečně nedávný platný příjem webových dat |
| `kotel_last_received_timestamp_seconds{device_id}` | UTC čas posledního příjmu, nikoli měření |
| `kotel_receive_age_seconds{device_id}` | Monotónně měřené stáří posledního příjmu |
| `kotel_collector_healthy{device_id}` | Zdraví hlavní smyčky a workerů |
| `kotel_mqtt_connected{device_id}` | Stav připojení MQTT |
| `kotel_session_state{device_id,state}` | Stav relace jako one-hot gauge |
| `kotel_reads_total`, `kotel_login_attempts_total`, `kotel_http_errors_total`, `kotel_session_losses_total` | Provozní čítače |
| `kotel_build_info{device_id,version}` | Verze collectoru |

Další `job` a `instance` doplní Prometheus. `up` udává úspěch scrape, ne čerstvost
měření kotle. Neznámé signály a texty nejsou exportovány jako metriky ani dynamické
labely. Přidejte je výslovně do mapování, pokud ověříte jejich typ a význam.

Po pauze/odhlášení zmizí měřené hodnoty z endpointu ihned při zpracování stavu, při
síťové chybě nejpozději po `telemetry.stale_s` od posledního příjmu. Dostupnost se
přepočítává při každém scrape, i když nepřicházejí nové vzorky. Chybějící čidlo,
`nepripojeno` nebo neplatný placeholder se nesmí změnit na platnou nulu.

V Grafaně zvolte svůj existující Prometheus datasource a importujte
[dashboard](../deploy/grafana-dashboard.json). Volitelný provisioning
[datasource](../deploy/grafana-datasource.yaml) používá `KOTEL_PROMETHEUS_URL` v prostředí
Grafany (např. `http://IP_PROMETHEA:9090`). Proměnné `job`, `instance`, `device_id`
oddělují zdroje. Časy se zobrazují podle prohlížeče; lze zvolit Europe/Prague.
Mezery se nespojují, ale krátké mezery může velký časový rozsah skrýt rozlišením grafu.
Logy událostí jsou strukturované na stdout; případné doručení do Loki zajistí váš
existující sběrač logů. Collector nepotřebuje vlastní Loki klient.

## Historie a výpadky

Historii a její retenci spravuje Prometheus. **Při výpadku Promethea není backfill.**
Collector nadále čte kotel a publikuje do MQTT, ale mezilehlé hodnoty nearchivuje.
Po obnovení scrape se načte pouze současný platný vzorek. Zastavení samotného collectoru
samozřejmě zastaví i čtení. Interval scrape nemusí přesně odpovídat každému HTTP vzorku.

`/data/state.sqlite` obsahuje jen pauzu, cooldown, diagnostiku, čítače a jeden poslední
snapshot. Není to historická fronta. Poslední raw odpověď zůstává pro diagnostiku,
ale po restartu se z ní nestane aktuální telemetrie. Úspěšné HTTP čtení nedokazuje
čerstvost regulátoru, známý JSON neobsahuje čas měření.

## MQTT a Home Assistant

Broker, TLS a prefix jsou konfigurovatelné. Výchozí prefix je
`kotelna/thematronic/2985`. HA musí mít MQTT integraci na stejném brokeru.

- `…/state`: současný JSON, non-retained, QoS 1, bez historického replay.
- `…/process`: retained stav procesu/MQTT, LWT `offline`.
- `…/availability`: retained dostupnost telemetrie.
- `homeassistant/.../config`: retained Discovery se stabilními ID.
- Na `homeassistant/status=online` znovu Discovery a pouze stále platný vzorek.

Dostupnost čidla vyžaduje proces, telemetrii a platnost konkrétní hodnoty. Senzory mají
navíc expiraci podle společného `telemetry.stale_s`. Stejná data používá exporter
i MQTT; změna platnosti tedy platí pro oba výstupy. QoS 1 připouští duplicity.
Nejasné booleany mají neutrální popis; `s_o_hav_term=true` není potvrzená havárie.

[HA dashboard](../deploy/ha-kotelna.yaml) nepotřebuje HACS. Při změně DeviceID změňte
i prefix a entity v dashboardu. Starou Discovery konfiguraci odstraněného signálu lze
smazat prázdnou retained zprávou na konkrétní `.../config` topic.

## Relace a ovládání

Výchozí `read_only` nevolá `DeviceSignalReload`. Volitelný `reload_then_read` používá
nastavené `reload_wait_s`; výchozí 2 s nejsou živě ověřené a odpověď 204 nepotvrzuje
nové měření. Endpointy pro ovládání kotle nejsou implementované.

Bez známé délky relace je každé potvrzené odhlášení následováno nejméně 300 s pauzou.
Při nastaveném `expected_session_lifetime_s` lze použít 30 s pouze když celý interval
poslední úspěch → první odmítnutí spadá do očekávané expirace ±60 s. Ztráta nové relace
do 120 s vždy vede k dlouhé pauze. Automatický odhad délky relace není implementovaný;
ručně ji kalibrujte alespoň ze tří konzistentních nerušených relací. Cookie expiry
není důkaz expirace serverové relace.

Neúspěšný login používá 5/10/20/40/60 minut backoff s malým jitterem. 429 respektuje
Retry-After. 5xx, timeout a poškozený JSON při čtení nezpůsobí nový login. Opakovaný
nejasný 403 vede k dlouhé pauze a diagnostice. Rozpoznané chybné přihlašovací údaje
vedou do AUTH_FAILED do restartu po opravě; skutečné texty chyby je třeba ověřit.

```sh
docker compose exec collector kotel-reader status
docker compose exec collector kotel-reader pause --minutes 30
docker compose exec collector kotel-reader resume
docker compose exec -T collector kotel-reader snapshot > latest-snapshot.json
```

Příkazy používají lokální stavovou DB. Pauza se převezme po právě běžícím HTTP požadavku
a zastaví i čekající login. Resume neruší serverový backoff. Provozujte jednu instanci
na účet; file lock hlídá společný `/data`, ne různé hosty/datové adresáře.

## Diagnostika selhání přihlášení

Nejprve zkontrolujte logy a stav běžícího kontejneru (název upravte podle Apps):

```sh
docker logs --tail 50 ix-kotel-logger-collector-1
docker exec ix-kotel-logger-collector-1 kotel-reader status
```

Od verze 0.2.1 obsahuje log chyby a `status.last_http_error` bezpečnou diagnostiku:
`stage` (`login_form`, `login_submit`, `device_signal`), metodu, případný HTTP status,
typ komprese a `exception_type`. Hesla, tokeny, cookies, těla odpovědí ani URL parametry
se do těchto údajů nezapisují. Poslední chyba přetrvá přes backoff/restart a smaže se
po přijetí platného vzorku.

`network_error` neznamená chybné heslo. Rozlišujte timeout/připojení/protokol podle
`exception_type`. `response_decode_error` znamená chybu dekódování odpovědi;
`login_form_not_found` problém rozpoznání formuláře; `login_not_confirmed` neověřenou
relaci. Pouze `invalid_credentials` je rozpoznaná odpověď o chybných údajích.

Ve verzi 0.2.0 byla chyba: po rozbalení gzip/deflate přes HTTPX se při vytvoření nové
odpovědi zachovala hlavička komprese a tělo se zkusilo rozbalit podruhé. Výsledkem mohl
být log `GET ... 200 OK` a vzápětí `network_error` ještě před odesláním formuláře.
Verze 0.2.1 toto opravuje; regresní testy ověřují komprimovaný formulář, redirect,
cookies i komprimovanou odpověď DeviceSignal.

Aktualizace přes Apps: Commit/Push opravy → **Publish container image** → počkat na
úspěšný běh → v **Edit YAML** nahradit `image` novým konkrétním `sha-...` tagem.
Nastavení secrets kvůli této opravě měnit nemusíte. Collector respektuje uložený
backoff, takže po aktualizaci může čekat do `next_attempt`. Nemažte `/data` jen kvůli
vynucení dalšího loginu. Nový pokus i jeho podrobnosti potom uvidíte v logu/statusu.

## Aktualizace z verze 0.1, záloha a rollback

1. Zastavte starý collector a zazálohujte jeho `/data` i případnou PostgreSQL historii.
2. Vycházejte z nového YAML. Odstraňte sekce `postgres`, `queue`; přesuňte
   `mqtt.stale_s` do `telemetry.stale_s`; přidejte `prometheus.host` a `prometheus.port`.
3. Odeberte PostgreSQL secret z nasazení a přidejte mapování portu pro scrape.
4. Nahraďte Grafana SQL dashboard novým Prometheus dashboardem a nastavte scrape job.
5. Při startu se jednorázově přenesou termíny cooldownu/pauzy a čítače ze starého
   `queue.sqlite` do `state.sqlite`. Starý soubor ani PostgreSQL se nemění či nemažou.
   Historická fronta se už nedosílá. Pokud je potřeba její obsah zachovat, před přechodem
   použijte export staré verze nebo nechte starý collector frontu doručit.

Nové snapshoty se nepřidávají do starého souboru. Starou frontu archivujte podle svých
potřeb. SQLite soubory zálohujte se zastaveným collectorem, včetně případných WAL;
nekopírujte pouze živý hlavní soubor. Prometheus data a konfiguraci zálohujte samostatně.
Secrets chraňte spolu s konfigurací. Při rollbacku na 0.1 obnovte i její kompatibilní
konfiguraci a uložený provozní stav; během změny nesmějí běžet dvě instance na jeden účet.

## Testy a živý checklist při zprovoznění

Lokálně `python -m pytest -q`: mock HTTP, fake clock a skutečný HTTP endpoint na loopbacku.
Testy mají zakázanou externí síť. `compose.test.yaml` spouští izolovaný Prometheus,
MQTT broker a syntetický exporter bez údajů ke kotli. Ověřuje i PromQL všech panelů.

```sh
docker build -t kotel-reader:0.2.1 .
docker compose -p kotel-reader-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kotel-reader-test -f compose.test.yaml down -v
```

- [ ] Dataset, ACL pro 10001, volná LAN IP/port a dostupnost brokeru.
- [ ] GitHub Actions úspěšně sestavily a ověřily kontejner.
- [ ] Prometheus target je UP, `kotel_telemetry_available` odpovídá skutečnému příjmu.
- [ ] Pauza/odhlášení odstraní teplotní série a HA čidla budou nedostupná.
- [ ] Restart collectoru neobnoví starý snapshot jako nový vzorek.
- [ ] Ověřit Reload, skutečnou délku relace a autentizační chyby se skutečným účtem.
- [ ] Ověřit význam signálů, polárnost booleanů a čitelnost dashboardů.
- [ ] Ověřit obnovu po výpadku MQTT/Promethea, zálohu a rollback.

Živé přihlášení ani nasazení na TrueNASu nebylo při vývoji provedeno. Automatizace
Thematronicu není potvrzená jako oficiálně podporovaná; collector neobchází limity.

## Rozšířená data Teploty / Stavy

Oba dodané výpisy mají shodných 130 názvů signálů. Kotel-logger už načítá celé pole DeviceSignal; rozšíření mění mapování, ne počet HTTP požadavků. Shodu s živým snapshotem kolektoru je ještě potřeba ověřit. Pro prostor zóny 2 se používá dev_prost_z2 (ve výpisu 21 °C), nikoliv nepřipojený s_t_zona2. Požadavky vody jsou s_t_zo1_voda_poz a s_t_zo2_voda_poz; jiné podobně pojmenované požadavky se neslučují.

Maxima se exportují odděleně; období a reset nejsou ověřeny. Booleany a kódy režimů mají surový význam k ověření, bez alarmových device_class. Textové režimy jdou pouze přes MQTT. Rezervované vstupy/výstupy a signály s neověřenou jednotkou zůstávají v diagnostickém snapshotu. Časování DeviceSignalReload a životnost relace zůstávají k ověření při zprovoznění.

Při aktualizaci zazálohujte a slučte nové config/signals.yaml s konfigurací připojenou na NASu (zachovejte vlastní úpravy), potom restartujte aplikaci. Nový image nepřepíše bind mount konfigurace. Mapování podporuje již verze 0.2.1. Pro nové panely znovu importujte deploy/grafana-dashboard.json. Prometheus job se jmenuje kotel-logger.
