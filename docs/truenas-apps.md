# TrueNAS Apps → Install via YAML (Prometheus + MQTT)

[← Úvod](../README.md) · [Provoz a metriky](operations.md)

Pro TrueNAS s Docker Apps (24.10+). Collector nepotřebuje PostgreSQL ani migrační
aplikaci. GitHub sestaví image, TrueNAS jej stáhne a spravuje přes Apps.

## 1. Připravit image na GitHubu

1. Přes TortoiseGit proveďte Commit + Push všech změn, včetně odstranění starých souborů.
2. Na GitHubu: **Actions → Publish container image → Run workflow**.
3. Počkejte na úspěšné testy a build. Ze souhrnu zkopírujte adresu
   `ghcr.io/alientab1/kotel-logger:sha-<12 znaků commitu>`.
4. Pro anonymní stažení nastavte v **Packages → kotel-logger → Package settings**
   viditelnost **Public**, pokud chcete image s kódem zveřejnit. Secrets v image nejsou.
   U soukromého balíčku místo toho nastavte přihlášení TrueNASu k `ghcr.io` přes token
   `read:packages` v nastavení registry odpovídajícím vaší verzi.

Workflow používá automatický `GITHUB_TOKEN`, nikoli credentials kotle/MQTT.
Spouští se ručně, publikuje konkrétní tag i `latest`. Pro nasazení použijte konkrétní
tag, který lze později vrátit. Veřejný repozitář sám nezaručuje veřejný image.

## 2. Připravit dataset a konfiguraci

V **Datasets** zvolte vlastní pool a dataset. Následující cesta je pouze šablona:

```text
/mnt/DOPLNTE_POOL/kotel-logger/
  config/
    config.yaml
    signals.yaml
  data/
  secrets/
    thematronic_username
    thematronic_password
    mqtt_password
```

Na hostitele přeneste [config.example.yaml](../config/config.example.yaml) jako
`config/config.yaml` a [signals.yaml](../config/signals.yaml). Přes existující
SFTP/SMB přístup lze soubory připravit bez klonování repozitáře na NAS.

V `config.yaml` doplňte IP/hostname MQTT brokeru a MQTT uživatele, případně TLS.
Ponechte `data_dir: /data`, `signals_file: signals.yaml`, `read_only`,
`expected_session_lifetime_s: null` a `telemetry.stale_s: 60`.
`prometheus.host: 0.0.0.0` a `port: 9108` nastavují listener uvnitř kontejneru.

Do tří secret souborů vložte jen příslušnou hodnotu bez uvozovek. ACL musejí umožnit
UID/GID `10001:10001` číst konfiguraci/secrets a zapisovat `data`; nadřazené adresáře
musí povolit průchod. Secrets neposkytujte ostatním uživatelům sdílení.

## 3. Importovat aplikaci

1. **Apps → Discover Apps → ⋮ → Install via YAML**.
2. Název aplikace: `kotel-logger`.
3. Vložte [truenas-app.yaml](../deploy/truenas-app.yaml).
4. Nahraďte `image` konkrétní adresou z Actions a **všechny** `/mnt/DOPLNTE_POOL/...`
   skutečnými cestami.
5. Nahraďte `host_ip: DOPLNTE_LAN_IP_NASU` skutečnou LAN IP hastrmana.
   Ověřte volný hostitelský port `9108`; pokud je obsazený, změňte `published`.
   Vnitřní `target: 9108` zůstane stejný jako `prometheus.port`.
6. Uložte a počkejte na stažení image a spuštění.

Použijte právě tento samostatný YAML: nepotřebuje `build: .`, relativní soubory ani
lokální `.env`. Kořenový `compose.yaml` je alternativa pro správu přes CLI.
Bridge síť stačí. Collector potřebuje odchozí HTTPS a MQTT; Prometheus potřebuje
přístup na publikovaný metrics port NASu. Endpoint je bez autentizace/TLS, určený
pro důvěryhodnou síť; nevystavujte jej do Internetu. Vlastní macvlan IP není potřeba.

## 4. Připojit existující Prometheus

Do konfigurace vašeho Promethea přidejte job z
[prometheus-scrape.yaml](../deploy/prometheus-scrape.yaml). Upravte target na skutečnou
LAN IP NASu a publikovaný port. Nepřepisujte stávající joby:

```yaml
scrape_configs:
  - job_name: kotel-reader
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    static_configs:
      - targets: ['DOPLNTE_LAN_IP_NASU:9108']
```

Proveďte validaci a reload/restart Promethea podle svého nasazení. V **Targets** musí
být target UP. Dotaz `kotel_temperature_celsius` vrátí platné teploty po úspěšném čtení.
Pokud Prometheus neběží, collector může stále číst a publikovat do MQTT, ale v historii
zůstane mezera. Žádná fronta ji později nedoplní.

## 5. Grafana a Home Assistant

V Grafaně importujte [dashboard JSON](../deploy/grafana-dashboard.json) a vyberte
existující **Prometheus datasource**. Volitelný [provisioning](../deploy/grafana-datasource.yaml)
používá `KOTEL_PROMETHEUS_URL` v prostředí Grafany.

V HA použijte MQTT integraci se stejným brokerem. Discovery vytvoří čidla; zobrazte je
na [stránce Kotelna](../deploy/ha-kotelna.yaml). Dostupnost vyžaduje platný nový příjem.

## Ověření a aktualizace

V Apps otevřete logy a shell kontejneru `collector`:

```sh
kotel-reader status
kotel-reader snapshot
kotel-reader pause --minutes 30
kotel-reader resume
```

`snapshot` je poslední diagnostický vzorek, může být starý. V `/metrics` se po 60 s bez
nového platného příjmu, při pauze nebo odhlášení teplotní série přestanou zveřejňovat.
Po restartu se starý snapshot automaticky nepoužije jako aktuální.

Aktualizace: Commit/Push → ručně **Publish container image** → **Edit YAML** v Apps →
nový image tag. Soubor na GitHubu sám konfiguraci již běžící aplikace nezmění.
Při přechodu z 0.1 použijte novou konfiguraci, odstraňte PostgreSQL secret a zrušte
starou migrační aplikaci, pokud jste ji měli. Žádná existující DB ani stará fronta se
automaticky nemaže; viz [postup aktualizace](operations.md).

Při `permission denied` ověřte ACL/UID 10001, při chybě pull tag a přihlášení/viditelnost
GHCR. Při nedostupném targetu ověřte správný port, NAS LAN IP a síť mezi Prometheem a NASem.
Živé nasazení na NAS a chování Thematronicu je stále potřeba ověřit při zprovoznění.

Zdroje: [TrueNAS Custom Apps](https://apps.truenas.com/managing-apps/installing-custom-apps/),
[publikování image](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images),
[GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
