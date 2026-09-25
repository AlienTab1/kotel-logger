# První nahrání projektu na GitHub

[← Úvod projektu](../README.md)

Postup počítá s projektem v `C:\ChatGPT\Kotel-Logger`, který ještě nemá lokální Git
repozitář, a s novým prázdným repozitářem na GitHubu.

## 1. Vytvořte prázdný repozitář

Na [GitHubu otevřete New repository](https://github.com/new), vyberte svůj účet, název
např. `kotel-reader` a požadovanou viditelnost. Nepřidávejte README, `.gitignore` ani
licenci přes formulář; první commit vytvoříte lokálně.

Zkopírujte HTTPS URL, například `https://github.com/TVUJ_UCET/kotel-reader.git`.

## 2. Připravte první commit v PowerShellu

Git je na tomto počítači dostupný v uvedené cestě; proměnná funguje i bez Gitu v PATH:

```powershell
Set-Location C:\ChatGPT\Kotel-Logger
$gitExe = 'C:\Program Files\Git\cmd\git.exe'
& $gitExe init -b main
& $gitExe add .
& $gitExe status --short
```

`.gitignore` vynechává `.venv`, `.env`, `secrets/`, `data/`, SQLite soubory a osobní
`config/config.yaml`. Zkontrolujte seznam připravených souborů a vytvořte commit:

```powershell
& $gitExe commit -m "Initial kotel-reader implementation"
```

Pokud Git nezná autora commitu, nastavte vlastní údaje pouze pro tento projekt:

```powershell
& $gitExe config user.name "Tvoje jmeno"
& $gitExe config user.email "TVUJ_EMAIL_NEBO_GITHUB_NOREPLY"
& $gitExe commit -m "Initial kotel-reader implementation"
```

## 3. Odešlete projekt

Nahraďte ukázkovou adresu HTTPS URL svého nového repozitáře:

```powershell
& $gitExe remote add origin https://github.com/TVUJ_UCET/kotel-reader.git
& $gitExe push -u origin main
```

Dokončete přihlášení nabídnuté správcem přihlašovacích údajů Gitu. Pokud vaše instalace
vyžaduje token, použijte GitHub personal access token; běžné heslo účtu pro Git přes
HTTPS nefunguje. Token nevkládejte do URL ani souborů projektu.

Po pushnutí GitHub zobrazí kořenový `README.md` jako úvod projektu. V **Actions**
najdete sestavení image a testy, pokud běh workflow povolují nastavení účtu/organizace.

## Další změny

Ve stejné PowerShell relaci a adresáři:

```powershell
& $gitExe add .
& $gitExe diff --cached --stat
& $gitExe commit -m "Popis zmeny"
& $gitExe push
```

Pokud vzdálený repozitář už obsahuje commity, nejprve slaďte historii; tento postup
počítá s prázdným repozitářem. Odmítnutý push neřešte automaticky pomocí `--force`.

Oficiální dokumentace:
[Nahrání lokálního projektu](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github),
[autentizace přes HTTPS](https://docs.github.com/en/get-started/git-basics/about-remote-repositories).
