# Initial project upload to GitHub

[← Project introduction](../README.md)

This initial-setup procedure assumes a project at `C:\ChatGPT\Kotel-Logger`
without a local Git repository and a new empty GitHub repository. The current
project already has Git initialized; use the subsequent-changes section for normal updates.
Git commands run at the repository root, while Docker commands run in `web-logger`.

## 1. Create an empty repository

Open [New repository on GitHub](https://github.com/new), choose your account,
a name such as `kotel-reader`, and the desired visibility. Do not add a README,
`.gitignore` or license in the form; create the initial commit locally.
Copy the HTTPS URL, for example `https://github.com/YOUR_ACCOUNT/kotel-reader.git`.

## 2. Prepare the first commit in PowerShell

Git is available at the path below on the development machine. The variable works
even if Git is not on PATH:

```powershell
Set-Location C:\ChatGPT\Kotel-Logger
$gitExe = 'C:\Program Files\Git\cmd\git.exe'
& $gitExe init -b main
& $gitExe add .
& $gitExe status --short
```

`.gitignore` excludes `.venv`, `.env`, `secrets/`, `data/`, SQLite files, personal
`config/config.yaml`, and private USB captures. Review staged files, then commit:

```powershell
& $gitExe commit -m "Initial kotel-reader implementation"
```

If the commit author is unset, configure your own details for this project:

```powershell
& $gitExe config user.name "Your name"
& $gitExe config user.email "YOUR_EMAIL_OR_GITHUB_NOREPLY"
& $gitExe commit -m "Initial kotel-reader implementation"
```

## 3. Push the project

Replace the example address with your new repository's HTTPS URL:

```powershell
& $gitExe remote add origin https://github.com/YOUR_ACCOUNT/kotel-reader.git
& $gitExe push -u origin main
```

Complete the sign-in offered by Git's credential manager. If a token is required,
use a GitHub personal access token; an ordinary account password does not work for
Git over HTTPS. Do not put the token in URLs or project files.

After the push, GitHub displays the root `README.md`. **Actions** provides image
builds and tests, subject to account/organization workflow permissions.

## Subsequent changes

In the same PowerShell session and repository directory:

```powershell
& $gitExe add .
& $gitExe diff --cached --stat
& $gitExe commit -m "Describe the change"
& $gitExe push
```

If the remote repository already contains commits, reconcile history first;
the initial procedure assumes an empty remote. Do not automatically resolve rejected pushes with `--force`.

Official documentation:
[Adding a local project](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github),
[HTTPS authentication](https://docs.github.com/en/get-started/git-basics/about-remote-repositories).
