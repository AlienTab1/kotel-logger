# Provozní příručka kotel-reader

[← Úvod projektu](../README.md) · [Nahrání na GitHub](github.md)

Příkazy a cesty v této příručce jsou relativní vůči kořeni projektu.

Čtecí služba v Pythonu 3.12+ pro Thematronic. Jeden Docker kontejner běží na TrueNASu,
čte telemetrii po 15 s, ukládá historii do **existujícího PostgreSQL** a současně
publikuje aktuální hodnoty do **existujícího MQTT brokeru** pro Home Assistant.
Nemá webové GUI ani ovládání kotle. Konfigurace je v YAML.

```text
Thematronic → collector → SQLite /data → PostgreSQL kotelna → Grafana
                  └──────────────────→ MQTT broker → Home Assistant
```

## Stav ověření

- Offline testy lze spustit bez účtu Thematronic; skutečné síťové spojení je v unit testech zakázáno.
- Implementace má testy přihlášení, relací, parsování, fronty, lokálního řízení a MQTT šablon.
- Samostatný `compose.test.yaml` ověřuje SQL, skutečný broker a opakovaný zápis po DB commitu.
  Tato integrační sada vyžaduje Docker. Je oddělená od produkční konfigurace i sítě.
- V prostředí prvního vývoje nebyl Docker dostupný: produkční image, Compose integrace
  a zobrazení dashboardů v reálném HA/Grafaně **dosud nejsou živě ověřené**.
- Skutečný účet/kotel nebyl kontaktován. Časování `DeviceSignalReload`, délka serverové
  relace, přesné texty chyb a fyzický význam nejasných signálů zbývají ověřit.

## Soubory a konfigurace

| Soubor | Účel |
|---|---|
| `config/config.example.yaml` → `config/config.yaml` | Připojení, intervaly, fronta, relace |
| `config/signals.yaml` | České názvy, typy, jednotky a publikování do HA |
| `.env.example` → `.env` | Cesta datasetu a souborů s přihlašovacími údaji |
| `compose.yaml` | Produkční collector, běžná bridge síť, žádné publikované porty |
| `compose.macvlan.yaml` | Volitelné připojení do existující `services_lan` |
| `migrations/` | Verzované SQL migrace, spouštěné samostatně |
| `deploy/` | Založení DB/rolí, granty, Grafana, ukázka HA dashboardu |

Připravte kopie:

```sh
cp config/config.example.yaml config/config.yaml
cp .env.example .env
mkdir -p secrets
```

Do `config.yaml` doplňte skutečnou IP MQTT brokeru na rarachovi a jeho uživatele.
PostgreSQL má přednastavenou adresu `172.30.1.122`, databázi `kotelna` a roli `kotel_writer`.
Ověřte je podle své infrastruktury. Pro provoz bez konkrétního výstupu nastavte jeho
`enabled: false`. Bez PostgreSQL se fronta plní do nastavených limitů.

Do čtyř souborů v `secrets/` vložte vždy pouze jednu hodnotu (bez uvozovek):

- `thematronic_username`
- `thematronic_password`
- `postgres_password`
- `mqtt_password`

Tyto soubory ani `.env` nepatří do repozitáře. Compose je připojí jako Docker secrets.
Na hostu nastavte ACL tak, aby je mohl číst UID **10001** v kontejneru a správce;
nepředpokládejte, že běžný Compose sám šifruje soubory na hostu. Prázdný soubor lze
použít pro heslo vypnutého výstupu, Compose ale vyžaduje existenci všech čtyř souborů.
Pro lokální vývoj fungují i proměnné `THEMATRONIC_USERNAME`, `THEMATRONIC_PASSWORD`,
`POSTGRES_PASSWORD`, `MQTT_PASSWORD`. Varianta `*_FILE` má přednost.

Po změně konfigurace restartujte kontejner. `validate` kontroluje hodnoty konfigurace,
nikoli dosažitelnost adres ani správnost hesel. Přihlašovací údaje nejsou vypisovány.

## PostgreSQL: samostatná databáze a role

Nesahejte do DB HA recorderu. Na stávajícím serveru jako administrátor jednorázově:

```sh
psql -h 172.30.1.122 -U ADMIN -d postgres -f deploy/create-database.sql
psql -h 172.30.1.122 -U ADMIN -d postgres
```

V interaktivním psql nastavte samostatná hesla:

```text
\password kotel_migrator
\password kotel_writer
\password kotel_grafana
\q
```

`create-database.sql` je jednorázový bootstrap, není určen k opakovanému spouštění.
Povolte připojení collectoru v `pg_hba.conf`/firewallu podle své sítě. Migrátor vlastní
schéma, collector má pouze SELECT/INSERT/DELETE (DELETE pro retenci) a Grafana SELECT.
Collector při startu **neprovádí migrace** a nepotřebuje DDL práva.

Po sestavení image spusťte migrace s dočasně připojeným heslem migrátora. Příklad pro
shell na hostu s Dockerem; `secrets/migration_password` připravte stejným způsobem:

```sh
docker compose build
docker compose run --rm --no-deps \
  -e POSTGRES_MIGRATION_USER=kotel_migrator \
  -e POSTGRES_MIGRATION_PASSWORD_FILE=/run/secrets/migration_password \
  -v "$PWD/secrets/migration_password:/run/secrets/migration_password:ro" \
  collector migrate
psql -h 172.30.1.122 -U kotel_migrator -d kotelna -f deploy/grants.sql
```

Migrace jsou transakční, evidované v `public.kotel_migrations` a chráněné advisory
lockem. Opakování už aplikovanou migraci přeskočí. Přístup migrátora nedávejte běžícímu collectoru.

Jeden cyklus znamená jeden řádek `kotel.snapshots`: stabilní UUID, zařízení, UTC čas
příjmu, nullable čas měření, raw JSONB, normalizované hodnoty a kvalita.
`kotel.session_events` obsahuje události bez hesel/tokenů. Views `kotel.temperatures`
a `kotel.binary_states` slouží dashboardům.

## TrueNAS / Docker Compose

Následující kroky provádějte až po skončení obnovy disků a ověření sítě.

1. Přeneste projekt do zvoleného adresáře na hastrmanovi.
2. Vytvořte vlastní persistentní dataset/adresář pro `/data`; cestu neodvozujte
   z příkladů. Nastavte zápis pro UID/GID `10001:10001` a vyplňte `KOTEL_DATA_DIR` v `.env`.
3. Připravte YAML, secrets, databázi a migrace podle předchozích částí.
4. Ověřte odchozí HTTPS na Thematronic, PostgreSQL a MQTT. Není potřeba otevřít port
   collectoru ani mu přidělovat vlastní IP.
5. Sestavte a spusťte:

```sh
docker compose config --quiet
docker compose build
docker compose run --rm collector validate
docker compose up -d collector
docker compose logs --tail=100 -f collector
docker compose exec collector kotel-reader status
```

Image běží jako non-root, root filesystem je read-only, `/data` je persistentní,
logy rotují po 10 MB (3 soubory), restart je `unless-stopped`. SIGTERM ukončuje workery;
Compose poskytuje 90 s pro dokončení krátkých operací. Healthcheck měří život hlavní
smyčky a workerů, nikoli dostupnost kotle. Cooldown a plánovaná pauza jsou zdravý stav.
Docker samotný kvůli označení `unhealthy` automaticky nerestartuje kontejner.

Macvlan používejte jen pokud jej chcete a máte ověřenou síť `services_lan` a volnou IP:

```sh
docker network inspect services_lan
# Teprve potom nastavte KOTEL_LAN_IP v .env.
docker compose -f compose.yaml -f compose.macvlan.yaml config --quiet
docker compose -f compose.yaml -f compose.macvlan.yaml up -d
```

Ověřte také dosažitelnost hostitele z macvlan. Konkrétní hostitelská cesta ani IP
collectoru není v projektu rezervována. Během vývoje nebylo na TrueNAS nic nasazováno.

## Home Assistant / MQTT

V HA nakonfigurujte integraci MQTT na stejný broker. Collector zveřejní retained
Discovery konfigurace se stabilními ID a společným zařízením `Kotel 2985`.
Teploty se objeví např. jako `sensor.thematronic_2985_s_t_spal`.
Již existující nebo uživatelem přejmenované entity mohou mít jiné ID.

- `kotelna/thematronic/2985/state`: JSON s aktuálním vzorkem, **non-retained**, QoS 1.
- `…/availability`: retained dostupnost telemetrie, offline při pauze, odhlášení
  a překročení 60 s od příjmu.
- `…/process`: retained stav MQTT spojení procesu, LWT `offline`.
- `homeassistant/.../config`: retained Discovery konfigurace.
- `homeassistant/status=online`: znovu nabídne Discovery a pouze dosud nedávný vzorek.

Dostupnost každého čidla vyžaduje zároveň proces, telemetrii a platnost konkrétního
signálu. Chybějící/odpojené čidlo se označí nedostupné; stará platná hodnota se nedrží.
Teplotní senzory mají navíc `expire_after`. Binární signály využívají stejnou řízenou
dostupnost a LWT. QoS 1 připouští duplicity. Při obnově připojení se publikuje pouze
nejnovější nedávný vzorek; DB replay nevolá MQTT publisher.

Broker musí povolit zápis pod zvoleným prefixem i `homeassistant/` a čtení
`homeassistant/status`. TLS nastavte podle brokeru; ověření certifikátu se nevypíná.

Do ručního dashboardu HA vložte `deploy/ha-kotelna.yaml`. Nepotřebuje HACS. Stav a
stáří příjmu jsou oddělené od čerstvosti samotného měření, která není ověřena.
U změny DeviceID upravte i MQTT prefix a dashboard. Staré retained Discovery entity
při odstranění/přejmenování signálu ručně odstraňte prázdnou retained zprávou na jejich
konkrétní `.../config` topic; automatické mazání jiných entit collector neprovádí.

## Grafana

Použijte PostgreSQL datasource s uživatelem `kotel_grafana` a DB `kotelna`.
`deploy/grafana-datasource.yaml` je volitelný provisioning příklad; hodnoty
`KOTEL_PG_HOST` a `KOTEL_GRAFANA_PASSWORD` patří do prostředí **Grafany**, ne collectoru.
Importujte `deploy/grafana-dashboard.json` a vyberte datasource.

Dashboard má proměnnou zařízení, teploty, neutrální raw booleany, poslední příjem,
mezery a události relací. Ukládání je UTC, zobrazení podle prohlížeče (lze nastavit
Europe/Prague). Dotazy respektují časový rozsah, agregují podle rozlišení a doplňují
prázdné časové buckety. Null hodnoty se nespojují. Kratší mezery uvnitř jednoho bucketu
se při velkém časovém rozsahu mohou sloučit; k diagnostice použijte panel mezer a kratší
rozsah. Grafana ani HA nemusí běžet, aby collector ukládal historii.

## Relace, pauzy a neověřené chování

Výchozí režim je `read_only`. Collector načte login formulář a jeho aktuální skrytá
pole, zachová celý cookie jar a login ověří platnou odpovědí `DeviceSignal`.
Pouhé 303 nestačí. Redirecty jsou omezené na zadaný HTTPS origin, TLS se ověřuje.

`reload_then_read` je volitelné explicitní nastavení. Volá pozorovaný
`DeviceSignalReload`, vyčká `reload_wait_s` a čte. Výchozí 2 s **nejsou ověřené časování**;
204 nedokazuje nové měření. Služba nemá žádný endpoint pro ovládání kotle.

Stavy: START, AUTHENTICATING, POLLING, AUTH_COOLDOWN, RETRY_BACKOFF, PAUSED, AUTH_FAILED.
Bez známé délky relace každé potvrzené odhlášení znamená nejméně 300 s pauzu.
Při ručně nastavené délce je rychlá pauza 30 s možná, jen pokud celý interval mezi
posledním úspěchem a prvním odmítnutím spadá do očekávané expirace ±60 s.
Ztráta nové relace do 120 s vždy znamená dlouhou pauzu. To omezuje přetahování
s ručním přihlášením, ale neurčuje jeho příčinu s jistotou.

Automatická kalibrace není v první verzi zapnutá ani implementovaná. Pro ruční
kalibraci pozorujte nejméně tři konzistentní nerušené relace (např. v noci), vyřaďte
síťové výpadky a relace s ručním loginem. Teprve poté lze nastavit
`expected_session_lifetime_s` na medián; při velkém rozptylu ponechte `null`.
Cookie expiry není důkaz délky serverové relace. Předčasné odhlášení konfiguraci nemění.

Po chybě nového loginu backoff 5/10/20/40/60 minut s malým jitterem, při 429 nejméně
Retry-After. 5xx/timeout/rozbitý JSON při čtení nevyvolávají nový login. Nejasný 403 po
třech pokusech vede k delší diagnostické pauze bez nového loginu. Explicitně rozpoznané
nesprávné credentials znamenají AUTH_FAILED až do restartu po opravě nastavení.
Přesné texty chyb webu ověřte; nerozpoznaná chyba formuláře zůstane přechodnou chybou
s dlouhým backoffem. Žádné preventivní přihlášení před expirací.

```sh
docker compose exec collector kotel-reader status
docker compose exec collector kotel-reader pause --minutes 30
docker compose exec collector kotel-reader resume
```

Příkazy jdou lokální SQLite frontou, bez veřejného administračního portu. Pauza se
uplatní po právě probíhajícím HTTP požadavku a zabrání dalším čtením i čekajícím loginům.
`resume` neruší serverový backoff ani cooldown. Pauza a čekací termíny přežijí restart;
cookies zůstávají pouze v paměti. Intervaly běhu používají monotonic clock, uložené
termíny UTC. Při změně hodin dozadu se čekání konzervativně prodlouží.

Pouštějte **jednu instanci na účet**. File lock zabrání druhé instanci nad stejným `/data`.
Není to distribuovaný zámek mezi různými hosty nebo nezávislými datovými adresáři.

## Výpadky, kapacita, retence a záloha

SQLite běží s WAL a `synchronous=FULL`. Vzorek se nejprve odolně uloží, poté ho worker
zapíše do PostgreSQL. Lokální ack přijde až po DB commitu. Pád mezi commitem a ack
znamená replay se stejným UUID, který PostgreSQL deduplikuje.

Frontu omezují čas (7 dní), počet (100 000) a objem serializovaných payloadů (256 MiB).
První dosažený limit rozhoduje. Při naplnění odstraňuje nejstarší položky, zapisuje
`queue_capacity_loss`/`queue_age_loss` do logu a zvyšuje persistentní `lost_items`.
Jedna nadměrná položka se odmítne s čítačem ztrát. Limity zahrnují i události.
Velikost SQLite souboru má navíc indexy, stránky a WAL; `max_bytes` není disková kvóta.
Uvolněné stránky se znovu používají. Dataset monitorujte a rezervujte prostor navíc.
Při nedostatku místa/chybě SQLite collector selže viditelně, neslibuje neuložené vzorky.

Export aktuálně čekajících položek (již doručená historie je v PostgreSQL):

```sh
docker compose exec -T collector kotel-reader export > kotel-fronta.jsonl
```

Výchozí retence DB je 90 dní. Collector průběžně maže starší vzorky i události v
omezených dávkách. Při 15 s vzniká 5 760 snapshotů denně, přibližně 2,1 milionu ročně.
Velikost závisí na celém payloadu a indexech; měřte `pg_total_relation_size`, skutečnou
velikost fronty a denní přírůstek. Projekt neobsahuje dlouhodobé agregované úložiště.

Pro zálohu `/data` collector zastavte a zkopírujte celý adresář včetně případných WAL
souborů; alternativou je konzistentní snapshot datasetu. PostgreSQL zálohujte samostatně
např. `pg_dump`. Chraňte také konfiguraci a secrets. Nekopírujte samotný živý SQLite
soubor bez WAL. Obnova se provádí se zastaveným collectorem a stejnými právy UID 10001.

Rollback: zastavte collector, vraťte předchozí označenou image a kompatibilní konfiguraci.
Zálohu `/data` obnovujte jen pokud je nutné, protože obnovuje i starší frontu a termíny.
DB migrace se automaticky nevracejí; před budoucí změnou schématu udělejte DB backup.
Nikdy kvůli rollbacku nemažte databázi HA ani běžící broker.

## Lokální vývoj a testy

Python 3.12+, zamčené přímé i tranzitivní závislosti v `requirements*.lock`:

```sh
python -m venv .venv
# Linux: source .venv/bin/activate ; Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.lock
python -m pytest -q
python -m kotel_reader --config config/config.example.yaml validate
```

Pro lokální běh zkopírujte konfiguraci, nastavte `data_dir` na svůj lokální adresář
a credentials přes proměnné/`*_FILE`, potom:

```sh
python -m kotel_reader --config config/config.yaml run
python -m kotel_reader --data-dir ./data status
```

Pouze poslední příkaz `run` s přístupovými údaji kontaktuje Thematronic; `validate`
ani testy se ke kotli nepřipojují. Kompletní kontejnerové ověření:

```sh
docker build -t kotel-reader:0.1.0 .
docker compose -p kotel-reader-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kotel-reader-test -f compose.test.yaml down -v
```

Testovací služby používají interní síť, testovací heslo a pomíjivou DB v tmpfs.
Nemají přístupové údaje ke kotli. CI workflow spouští stejné kroky. Dashboard lze
znovu vytvořit `python tools/generate_dashboard.py`.

## Živý checklist při zprovoznění

- [ ] Síť, dataset, práva non-root uživatele; žádný zásah do obnovovaných disků.
- [ ] Login ověřen skutečným DeviceSignal; potvrzené i chybové odpovědi zdokumentované bez secrets.
- [ ] V `read_only` pozorovat, zda se hodnoty mění. Pokud ne, ověřit browserový záznam
      pořadí Reload → Signal a vhodnou prodlevu. Neměnné hodnoty samy nejsou důkaz chyby.
- [ ] Ověřit skutečné trvání relace bez ručních loginů, pak opatrně nastavit očekávanou délku.
- [ ] Vyzkoušet ruční login a ověřit 300s pauzu collectoru, bez přetahování o účet.
- [ ] Ověřit PostgreSQL role, migrace, výpadek a doslání fronty bez duplicit.
- [ ] Ověřit MQTT Discovery, restart HA/brokeru, nedostupné čidlo a stárnutí dat.
- [ ] Ověřit české názvy, skutečný typ a polaritu dalších signálů před `publish: true`.
- [ ] Importovat Grafanu a HA dashboard; ověřit časy, mezery a dlouhé časové rozsahy.
- [ ] Ověřit pause/resume, restart během cooldownu, zálohu a obnovu `/data`.

Automatizace Thematronicu není potvrzená jako oficiálně podporovaná. Collector neobchází
limity a nepoužívá cookies ani tokeny ze screenshotů.

Referenční dokumentace:
[HTTPX](https://www.python-httpx.org/advanced/clients/),
[Psycopg](https://www.psycopg.org/psycopg3/docs/basic/usage.html),
[Paho MQTT](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html),
[HA MQTT sensor](https://www.home-assistant.io/integrations/sensor.mqtt/),
[HA MQTT binary sensor](https://www.home-assistant.io/integrations/binary_sensor.mqtt/),
[Grafana PostgreSQL](https://grafana.com/docs/grafana/latest/datasources/postgres/).
