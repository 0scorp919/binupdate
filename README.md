# binupdate — DevOps CLI Bin Manager

Менеджер автооновлення DevOps CLI інструментів у Autonomous Capsule.

**Поточна версія:** `bin_manager.py` v1.5

## Запуск

### 🔵 Бойовий лаунчер (системний, для щоденного використання)

```
Win+R → bin
```

або напряму:

```
tags\bin.bat
```

- Хардкод шляхів `C:\!Oleksii_Rovnianskyi\...`
- Реєструється у системному PATH через `fix_path.ps1`
- **Не публікується** в GitHub (містить абсолютні шляхи)

### 🟢 Портативний лаунчер (GitHub-ready, для публікації)

```
devops\binupdate\bin_launcher.bat
```

- Auto-detect `CAPSULE_ROOT` від `%~dp0` (без хардкодованих шляхів)
- Працює з будь-якого розташування capsule
- **Публікується** в GitHub разом з проектом

## Портативність

`bin_manager.py` v1.5 використовує `CAPSULE_ROOT` auto-detect:

```python
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CAPSULE_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
```

Структура: `devops/binupdate/bin_manager.py` → два рівні вгору → `CAPSULE_ROOT`.
Хардкодовані абсолютні шляхи відсутні — проект працює з будь-якого розташування.

## Алгоритм роботи

1. **Очищення логів** — ротація активного лога (>50 MB → part-файл), видалення файлів старших за 7 днів або більших за 10 MB (поточний день захищений)
2. **Перевірка та оновлення кожного інструменту:**
   - Визначення встановленої версії (`--version` / `version --short` / JSON)
   - Запит останньої версії через GitHub Releases API або Kubernetes CDN
   - Якщо є оновлення: завантаження → розпакування → копіювання в `apps/bin/`
3. **Перевірка системного PATH** — `apps/bin/` у HKLM PATH, UAC → `fix_path.ps1 -AutoClose`

> **Примітка:** Резервна копія не потрібна — CLI-інструменти без даних користувача.

## Структура файлів

```
devops/binupdate/
  bin_manager.py     — головний менеджер (v1.5)
  bin_launcher.bat   — портативний лаунчер (GitHub-ready, auto-detect CAPSULE_ROOT)
  .env               — конфігурація GITHUB_TOKEN (gitignored)
  .env.example       — шаблон змінних середовища
  .gitignore         — виключення для Git
  README.md          — ця документація

tags/
  bin.bat            — системний лаунчер (UAC elevation → bin_manager.py)
                       містить хардкод шляхів, не публікується в GitHub

apps/bin/            — цільова папка (у системному PATH)
  helm.exe
  kubectl.exe
  terraform.exe
  rclone.exe
  gh.exe
  bw.exe

logs/binlog/
  bin_log_YYYY-MM-DD.log   — щоденні логи (ротація 7 днів + 10 MB)
```

### Відмінність bin_launcher.bat від tags/bin.bat

- `tags/bin.bat` — системний лаунчер: хардкод `C:\!Oleksii_Rovnianskyi\...`, реєструється у PATH через `fix_path.ps1`, **не публікується** в GitHub
- `bin_launcher.bat` — GitHub-ready: auto-detect `CAPSULE_ROOT` від `%~dp0`, лежить поруч з менеджером, **публікується** в GitHub разом з проектом

## Інструменти

- `helm.exe` — Менеджер застосунків Kubernetes
  - Джерело: `github.com/helm/helm`
  - Метод: Releases API → zip
- `kubectl.exe` — CLI для Kubernetes
  - Джерело: `dl.k8s.io` CDN
  - Метод: прямий `.exe`
- `terraform.exe` — Інфраструктура як код (IaC)
  - Джерело: `github.com/hashicorp/terraform`
  - Метод: Releases API → zip
- `rclone.exe` — Синхронізація з хмарними сховищами
  - Джерело: `github.com/rclone/rclone`
  - Метод: Releases API → zip
- `gh.exe` — GitHub CLI
  - Джерело: `github.com/cli/cli`
  - Метод: Releases API → zip
- `bw.exe` — Bitwarden CLI
  - Джерело: `github.com/bitwarden/clients`
  - Метод: Releases API (filtered by tag) → zip

## Стратегія оновлення

### helm, terraform, rclone, gh — GitHub Releases API (стандартний)

```
GET https://api.github.com/repos/{owner}/{repo}/releases/latest
→ tag_name: "v3.17.0"
→ assets[].browser_download_url → фільтр за regex pattern
→ download .zip → extract .exe → copy to apps/bin/
```

### kubectl — Kubernetes CDN

```
GET https://dl.k8s.io/release/stable.txt → "v1.32.2"
GET https://dl.k8s.io/release/v1.32.2/bin/windows/amd64/kubectl.exe
→ copy to apps/bin/kubectl.exe
```

### bw — GitHub Releases API з фільтрацією тегів

```
GET https://api.github.com/repos/bitwarden/clients/releases?per_page=20
→ фільтр: не prerelease, не draft, tag_name містить "cli"
→ tag_name: "cli-v2026.1.0" → версія: "2026.1.0"
→ assets[].browser_download_url → фільтр: bw-windows-*.zip
→ download bw-windows-2026.1.0.zip → extract bw.exe → copy to apps/bin/
```

> **Чому не `/releases/latest` для bw:**
> Репо `bitwarden/clients` містить кілька продуктів в одному репо.
> `/releases/latest` повертає найновіший реліз незалежно від продукту
> (може бути `desktop-v*` або `browser-v*`).
> `get_latest_version_github_filtered()` перебирає список релізів
> та повертає перший не-prerelease з `"cli"` в `tag_name`.

### Особливості розпакування

- `helm` — `windows-amd64/helm.exe`
- `terraform` — `terraform.exe` (корінь zip)
- `rclone` — `rclone-v*-windows-amd64/rclone.exe` (пошук по імені)
- `kubectl` — прямий `.exe`, без zip
- `gh` — `bin/gh.exe`
- `bw` — `bw.exe` (корінь zip)

## Визначення версій

- `helm` — `helm version --short` → `v3.17.0+...` → `3.17.0`
- `kubectl` — `kubectl version --client --output=json` → JSON `clientVersion.gitVersion`
- `terraform` — `terraform version -json` → JSON `{"terraform_version": "1.10.5"}`
- `rclone` — `rclone version --check=false` → `rclone v1.68.2` → `1.68.2`
- `gh` — `gh --version` → `gh version 2.x.x` → `2.x.x`
- `bw` — `bw --version` → `2026.1.0`

## Налаштування GITHUB_TOKEN (рекомендовано)

Файл `devops/binupdate/.env` (створити з `.env.example`):

```env
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxxxxxx
```

- **Без токена:** 60 req/год (ризик 403 при частих запусках або спільному IP)
- **З токеном:** 5000 req/год — повністю знімає rate limit
- **Права:** Public Repositories read-only, Contents + Metadata

Менеджер робить ~3 запити на інструмент × 6 інструментів = ~18 запитів за запуск.

## Логи

- **Шлях:** `logs/binlog/bin_log_YYYY-MM-DD.log`
- **Ротація активного лога:** >50 MB → перейменування у `_part2`, `_part3`... (поточний день не видаляється)
- **Ротація старих логів:** 7 днів + 10 MB (поточний день захищений)
- **Формат:** `YYYY-MM-DD HH:MM:SS [INFO] повідомлення`
- **Дублювання:** файл + stdout (ANSI кольори в консолі)

## Аргументи CLI

```
python bin_manager.py [--install-only]
```

- `--install-only` — оновлення без автозакриття (для автоматизації)

## Залежності

Python-бібліотеки (self-healing pip install):
- `requests` — HTTP-запити до GitHub API та CDN
- `packaging` — коректне порівняння версій

## PATH

`apps/bin/` реєструється в системному PATH через `devops/pathupdate/fix_path.ps1`.
Менеджер автоматично перевіряє наявність `apps\bin\` у `HKLM` PATH при кожному запуску.
Якщо відсутній — запускає `fix_path.ps1 -AutoClose` з UAC.

## Troubleshooting

**Інструмент не знайдено після оновлення:**

```cmd
Win+R → bin
```

або вручну:

```powershell
<CAPSULE_ROOT>\apps\pwsh\pwsh.exe -File <CAPSULE_ROOT>\devops\pathupdate\fix_path.ps1
```

**GitHub API rate limit (60 req/год без токена):**
- Менеджер перехоплює `HTTP 403/429` та виводить попередження
- Рішення: зачекати або додати `GITHUB_TOKEN` у `.env`

**bw: версія не визначається (0.0.0):**
- `bw --version` виводить лише число без префіксу `v` (наприклад `2026.1.0`)
- Загальний regex `v?(\d+\.\d+\.\d+)` коректно парсить цей формат

**bw: asset не знайдено:**
- Перевір що `tag_filter: "cli"` відповідає поточному формату тегів у `bitwarden/clients`
- Актуальний формат: `cli-v2026.1.0`. Якщо Bitwarden змінить формат — оновити `tag_filter`

**kubectl: `--short` deprecated у нових версіях:**
- Менеджер парсить повний вивід `kubectl version --client --output=json`

**terraform version -json не спрацював:**
- Fallback: парсинг текстового виводу `terraform version`

**Завантаження не вдалося:**
- Менеджер робить 3 спроби з exponential backoff (2s, 4s)
- Перевір лог у `logs/binlog/`

**PATH не оновлюється:**
- Запусти `Win+R → bin` з правами адміністратора (UAC)
- Або вручну: `devops/pathupdate/fix_path.ps1`

## CHANGELOG

- **v1.5** — Підготовка до публікації на GitHub: `CAPSULE_ROOT` auto-detect (замінено хардкод `USER_ROOT`), `cleanup_old_logs` захист поточного дня (`today_str`), `_rotate_log_if_needed()` (>50 MB → part-файл), `bin_launcher.bat` (GitHub-ready портативний лаунчер), `.gitignore`
- **v1.4** — Стандарт менеджера капсули: `__version__` + `get_manager_hash()`, `cleanup_old_logs(max_size_mb=10.0)`, динамічний заголовок, `_parse_env_file()` + `GITHUB_TOKEN`, `.env.example`
- **v1.3** — Додано `bw.exe` (Bitwarden CLI): `github_filtered` source, `get_latest_version_github_filtered()`
- **v1.2** — Додано `gh.exe` (GitHub CLI): `cli/cli`, `bin/gh.exe` всередині zip
- **v1.1** — Фікси: `stdin=DEVNULL`, retry x3, `zipfile.is_zipfile()` validation, terraform asset pattern
- **v1.0** — Початкова версія: helm, kubectl, terraform, rclone
