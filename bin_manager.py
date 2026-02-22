# -*- coding: utf-8 -*-
"""
DevOps CLI Bin Manager (v1.6)
Author: Oleksii Rovnianskyi System

UA: Менеджер DevOps CLI інструментів (apps/bin/).
    - Перевірка поточних версій: helm, kubectl, terraform, rclone, gh, bw, sqlite3
    - Перевірка нових версій через офіційні GitHub Releases API
    - Завантаження та оновлення бінарників
    - Ротація логів (7 днів + 50 MB; поточний день захищений)
    - НЕ робить бекап — CLI-інструменти без даних користувача
    - GITHUB_TOKEN з .env — знімає rate limit (60 → 5000 req/год)
    - Портативність: SCRIPT_DIR → CAPSULE_ROOT auto-detect (хардкод заборонено)
    - sqlite3.exe — читання VS Code globalStorage (state.vscdb) для auto-config

CHANGELOG:
    v1.6 — Додано sqlite3.exe (SQLite CLI):
           Джерело: github.com/sqlite/sqlite-amalgamation (precompiled binaries)
           Використовується clinecli_manager.py для читання OpenRouter API ключа
           з VS Code globalStorage/state.vscdb (Cline extension settings)
           Додано до TOOLS: sqlite3, asset: sqlite-tools-win-x64-*.zip
    v1.5 — Підготовка до публікації на GitHub (аудит портативності):
           CAPSULE_ROOT auto-detect від SCRIPT_DIR (замінено хардкод USER_ROOT)
           cleanup_old_logs: захист поточного дня (today_str перевірка)
           _rotate_log_if_needed(): якщо активний лог > 50 MB → part-файл
           Додано bin_launcher.bat — GitHub-ready портативний лаунчер
           Додано .gitignore
    v1.4 — Стандарт менеджера капсули:
           __version__ + get_manager_hash() (SHA256 self-check)
           cleanup_old_logs: додано max_size_mb=10.0 (7 днів + 10 MB)
           Заголовок консолі: динамічний v{__version__} + hash
           _parse_env_file() + GITHUB_TOKEN підтримка (знімає rate limit)
           get_latest_version_github/get_asset_url: передають Authorization header
    v1.3 — Додано Bitwarden CLI (bw.exe):
           Джерело: github.com/bitwarden/clients
           Фільтр тегів: tag_name містить "cli" (репо має теги desktop-v*, browser-v*, cli-v*)
           Asset: bw-windows-{ver}.zip → bw.exe в корені архіву
           Версія: bw --version → "2026.1.0"
           Нова функція: get_latest_version_github_filtered(repo, tag_filter)
    v1.2 — Додано GitHub CLI (gh.exe):
           Джерело: github.com/cli/cli, asset: gh_*_windows_amd64.zip
           Бінарник: bin/gh.exe всередині zip
           Версія: gh version → "gh version 2.x.x"
    v1.1 — Фікси:
           1. subprocess.run: stdin=DEVNULL (Input redirection not supported)
           2. download_file: retry x3 + zipfile.is_zipfile() validation
           3. terraform asset_pattern: виправлено _ замість - перед windows
    v1.0 — Початкова версія.
           Джерела:
             helm      → github.com/helm/helm
             kubectl   → dl.k8s.io (офіційний CDN Kubernetes)
             terraform → github.com/hashicorp/terraform
             rclone    → github.com/rclone/rclone
"""
import os
import sys
import hashlib
import subprocess
import time
import datetime
import logging
import glob
import re
import zipfile
import shutil

__version__ = "1.6"


def get_manager_hash() -> str:
    """Return first 12 chars of SHA256 of this script file (self-integrity check).
    UA: Повертає перші 12 символів SHA256 власного файлу (self-check цілісності)."""
    try:
        with open(os.path.abspath(__file__), 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception:
        return "????????????"


# ---------------------------------------------------------------------------
# КОНФІГУРАЦІЯ
# ---------------------------------------------------------------------------
# UA: v1.5 — CAPSULE_ROOT auto-detect від SCRIPT_DIR (хардкод заборонено).
#     Структура: devops/binupdate/bin_manager.py → два рівні вгору → CAPSULE_ROOT
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
CAPSULE_ROOT  = os.path.dirname(os.path.dirname(SCRIPT_DIR))

BIN_DIR       = os.path.join(CAPSULE_ROOT, "apps", "bin")
LOG_DIR       = os.path.join(CAPSULE_ROOT, "logs", "binlog")
DOWNLOADS_DIR = os.path.join(CAPSULE_ROOT, "downloads")
ENV_FILE      = os.path.join(SCRIPT_DIR, ".env")

PYTHON_EXE  = sys.executable
START_TIME  = time.time()

# UA: Конфігурація кожного інструменту
TOOLS: list[dict] = [
    {
        "name":    "helm",
        "exe":     "helm.exe",
        "desc":    "Менеджер застосунків Kubernetes. Встановлює та керує застосунками в кластері.",
        "source":  "github",
        "repo":    "helm/helm",
        "asset_pattern": r"helm-v[\d\.]+-windows-amd64\.zip",
        "binary_in_zip": "windows-amd64/helm.exe",
    },
    {
        "name":    "kubectl",
        "exe":     "kubectl.exe",
        "desc":    "Інтерфейс командного рядка для Kubernetes. Керування кластером та застосунками.",
        "source":  "k8s",
        "version_url":  "https://dl.k8s.io/release/stable.txt",
        "download_url": "https://dl.k8s.io/release/{version}/bin/windows/amd64/kubectl.exe",
    },
    {
        "name":    "terraform",
        "exe":     "terraform.exe",
        "desc":    "Інструмент для інфраструктури як код (IaC). Автоматизує створення хмарних ресурсів.",
        "source":  "github",
        "repo":    "hashicorp/terraform",
        # UA: ФІКС v1.1 — underscore перед windows, не hyphen
        # Реальний файл: terraform_1.14.5_windows_amd64.zip
        "asset_pattern": r"terraform_[\d\.]+_windows_amd64\.zip",
        "binary_in_zip": "terraform.exe",
    },
    {
        "name":    "rclone",
        "exe":     "rclone.exe",
        "desc":    "Синхронізація файлів з хмарними сховищами. Підтримує шифрування та багато провайдерів.",
        "source":  "github",
        "repo":    "rclone/rclone",
        "asset_pattern": r"rclone-v[\d\.]+-windows-amd64\.zip",
        "binary_in_zip": None,  # UA: rclone.exe лежить у підпапці rclone-v*-windows-amd64/
    },
    {
        "name":    "gh",
        "exe":     "gh.exe",
        "desc":    "GitHub CLI. Керування репозиторіями, issues та PR прямо з терміналу.",
        "source":  "github",
        "repo":    "cli/cli",
        # UA: Реальний файл: gh_2.x.x_windows_amd64.zip
        "asset_pattern": r"gh_[\d\.]+_windows_amd64\.zip",
        # UA: Всередині zip: bin/gh.exe
        "binary_in_zip": "bin/gh.exe",
    },
    {
        "name":    "bw",
        "exe":     "bw.exe",
        "desc":    "Bitwarden CLI. Безпечне керування паролями та секретами через термінал.",
        # UA: bitwarden/clients містить теги desktop-v*, browser-v*, cli-v*
        #     /releases/latest повертає не CLI реліз — потрібна фільтрація по tag_name
        "source":  "github_filtered",
        "repo":    "bitwarden/clients",
        "tag_filter": "cli",  # UA: фільтр: tag_name.lower() містить "cli"
        # UA: Реальний файл: bw-windows-2026.1.0.zip
        "asset_pattern": r"bw-windows-[\d\.]+\.zip",
        # UA: Всередині zip: bw.exe (в корені архіву)
        "binary_in_zip": "bw.exe",
    },
    {
        "name":    "sqlite3",
        "exe":     "sqlite3.exe",
        "desc":    "SQLite CLI. Читання .vscdb баз даних VS Code (globalStorage) для auto-config.",
        # UA: v1.6 — sqlite3 використовується clinecli_manager.py для читання
        #     OpenRouter API ключа з state.vscdb (Cline extension settings).
        #     Джерело: sqlite.org precompiled binaries для Windows x64.
        "source":  "sqlite_org",
        # UA: Пряме завантаження з sqlite.org/download.html
        # Формат: sqlite-tools-win-x64-XXXXXXX.zip → sqlite3.exe в корені архіву
        "asset_pattern": r"sqlite-tools-win-x64-[\d]+\.zip",
        "binary_in_zip": None,  # UA: sqlite3.exe шукається у будь-якому місці архіву
    },
]

# ---------------------------------------------------------------------------
# КОЛЬОРИ
# ---------------------------------------------------------------------------
os.system('')  # UA: Вмикаємо ANSI-кольори в Windows CMD

class Colors:
    HEADER = '\033[95m'
    BLUE   = '\033[94m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    RESET  = '\033[0m'
    BOLD   = '\033[1m'

def cprint(msg: str, color: str = Colors.RESET, end: str = "\n") -> None:
    """Print colored message to stdout. UA: Виводить кольоровий текст."""
    sys.stdout.write(color + msg + Colors.RESET + end)
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# ЗАЛЕЖНОСТІ (self-healing)
# ---------------------------------------------------------------------------
def ensure_dependencies() -> None:
    """Install missing pip packages automatically. UA: Автовстановлення залежностей."""
    required = {'requests', 'packaging'}
    missing = [lib for lib in required if not _can_import(lib)]
    if missing:
        cprint(f"[SETUP] Докачую бібліотеки: {', '.join(missing)}...", Colors.YELLOW)
        try:
            subprocess.check_call(
                [PYTHON_EXE, "-m", "pip", "install"] + missing,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,  # UA: ФІКС v1.1 — Input redirection
            )
        except Exception as e:
            cprint(f"[SETUP] Помилка встановлення: {e}", Colors.RED)

def _can_import(name: str) -> bool:
    """Check if module is importable. UA: Перевіряє чи можна імпортувати модуль."""
    try:
        __import__(name)
        return True
    except ImportError:
        return False

ensure_dependencies()

import requests          # type: ignore
from packaging import version  # type: ignore

# ---------------------------------------------------------------------------
# ЛОГУВАННЯ
# ---------------------------------------------------------------------------
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"bin_log_{datetime.date.today()}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8')]
)

def log(msg: str, color: str = Colors.RESET, console: bool = True) -> None:
    """Log to file and optionally to console. UA: Логує у файл і консоль."""
    logging.info(msg)
    if console:
        cprint(msg, color)

# ---------------------------------------------------------------------------
# УТИЛІТИ
# ---------------------------------------------------------------------------
def draw_progress(label: str, percent: int, width: int = 20) -> None:
    """Draw ASCII progress bar. UA: Малює прогрес-бар."""
    bars = int(percent / (100 / width))
    bar = '=' * bars + '.' * (width - bars)
    sys.stdout.write(f"\r{Colors.YELLOW}{label}: [{bar}] {percent}%{Colors.RESET}")
    sys.stdout.flush()

def download_file(url: str, save_path: str, label: str = "Download",
                  retries: int = 3) -> None:
    """
    Download file with progress bar and retry logic.
    UA: Завантажує файл з прогрес-баром та повторними спробами.
        ФІКС v1.1: retry x3 + перевірка Content-Type (не HTML).
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/octet-stream, application/zip, */*',
    }
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=120,
                              allow_redirects=True, headers=headers) as r:
                r.raise_for_status()

                # UA: ФІКС v1.1 — перевіряємо що отримали бінарник, не HTML
                content_type = r.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    raise ValueError(
                        f"Сервер повернув HTML замість файлу "
                        f"(Content-Type: {content_type}). "
                        f"Можливо GitHub redirect не спрацював."
                    )

                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            draw_progress(f"   {label}", int(downloaded * 100 / total))
            print("")
            return  # UA: Успішно — виходимо

        except Exception as e:
            last_error = e
            if attempt < retries:
                log(f"   ⚠️ Спроба {attempt}/{retries} невдала: {e}. Повтор...",
                    Colors.YELLOW)
                # UA: Видаляємо пошкоджений файл перед повтором
                if os.path.exists(save_path):
                    try:
                        os.remove(save_path)
                    except Exception:
                        pass
                time.sleep(2 * attempt)  # UA: Exponential backoff: 2s, 4s
            else:
                raise RuntimeError(
                    f"Завантаження не вдалося після {retries} спроб: {last_error}"
                ) from last_error

def validate_zip(path: str) -> bool:
    """
    Validate that file is a valid ZIP archive.
    UA: ФІКС v1.1 — перевіряє що файл є валідним ZIP перед розпакуванням.
    """
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 22:  # UA: Мінімальний розмір ZIP (End of Central Directory)
        return False
    return zipfile.is_zipfile(path)

# ---------------------------------------------------------------------------
# .ENV ПАРСЕР
# ---------------------------------------------------------------------------
def _parse_env_file() -> dict:
    """Parse .env file into key→value dict, skipping comments and blanks.
    UA: Парсить .env у словник, ігноруючи коментарі та порожні рядки."""
    result: dict = {}
    if not os.path.exists(ENV_FILE):
        return result
    try:
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    result[key.strip()] = val.strip()
    except Exception:
        pass
    return result


def _get_github_headers() -> dict:
    """Build GitHub API headers, adding Authorization if GITHUB_TOKEN is set in .env.
    UA: Формує заголовки GitHub API. Якщо GITHUB_TOKEN є у .env — додає Authorization.
        Знімає rate limit: 60 req/год (анонімно) → 5000 req/год (з токеном)."""
    env_vars = _parse_env_file()
    token = env_vars.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# КРОК 1: Ротація логів
# ---------------------------------------------------------------------------
def _rotate_log_if_needed(log_path: str, max_size_mb: float = 50.0) -> None:
    """Rotate active log file if it exceeds max_size_mb by renaming to _part2, _part3...
    UA: Якщо активний лог > max_size_mb — перейменовує у _part2, _part3...
        Поточний день ніколи не видаляється (стандарт капсули)."""
    if not os.path.exists(log_path):
        return
    if os.path.getsize(log_path) < max_size_mb * 1024 * 1024:
        return
    base, ext = os.path.splitext(log_path)
    part = 2
    while os.path.exists(f"{base}_part{part}{ext}"):
        part += 1
    try:
        os.rename(log_path, f"{base}_part{part}{ext}")
        cprint(f"   ♻️  Лог ротовано → {os.path.basename(base)}_part{part}{ext}", Colors.YELLOW)
    except Exception:
        pass


def cleanup_old_logs(days: int = 7, max_size_mb: float = 10.0) -> None:
    """Remove log files older than N days or larger than max_size_mb MB.
    UA: Видаляє логи старші за N днів або більші за max_size_mb МБ.
        Стандарт капсули: 7 днів + 10 MB. Поточний день захищений."""
    log("🧹 Перевірка старих логів...", Colors.CYAN)
    # UA: v1.5 — ротація активного лога якщо > 50 MB (перед очищенням)
    _rotate_log_if_needed(LOG_FILE, max_size_mb=50.0)
    now = time.time()
    cutoff = now - (days * 86400)
    max_size_bytes = max_size_mb * 1024 * 1024
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
    deleted = 0
    for f in log_files:
        # UA: v1.5 — поточний день ніколи не видаляється
        if today_str in os.path.basename(f):
            continue
        try:
            stat = os.stat(f)
            if stat.st_mtime < cutoff or stat.st_size > max_size_bytes:
                reason = "застарілий" if stat.st_mtime < cutoff else f">{max_size_mb:.0f} MB"
                os.remove(f)
                deleted += 1
                log(f"   🗑️ Видалено лог ({reason}): {os.path.basename(f)}", Colors.YELLOW)
        except Exception:
            pass
    if deleted > 0:
        log(f"✅ Очищено логів: {deleted}", Colors.GREEN)
    else:
        log("   ✨ Старих логів немає.", Colors.GREEN)

# ---------------------------------------------------------------------------
# КРОК 2: Перевірка та оновлення кожного інструменту
# ---------------------------------------------------------------------------
def get_installed_version(tool: dict) -> str:
    """
    Get installed version of a CLI tool.
    UA: Отримує встановлену версію CLI інструменту через --version або version.
        ФІКС v1.1: stdin=DEVNULL для всіх subprocess.run.
    """
    exe_path = os.path.join(BIN_DIR, tool["exe"])
    if not os.path.exists(exe_path):
        return "0.0.0"

    version_args = {
        "helm":      ["version", "--short"],
        "kubectl":   ["version", "--client", "--output=json"],
        "terraform": ["version", "-json"],
        "rclone":    ["version", "--check=false"],
    }
    args = version_args.get(tool["name"], ["--version"])

    try:
        result = subprocess.run(
            [exe_path] + args,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,  # UA: ФІКС v1.1 — Input redirection not supported
        )
        output = result.stdout + result.stderr

        # UA: kubectl повертає JSON — парсимо окремо
        if tool["name"] == "kubectl":
            try:
                import json
                data = json.loads(result.stdout)
                ver_str = (data.get("clientVersion", {}).get("gitVersion", "") or
                           data.get("kustomizeVersion", ""))
                m = re.search(r"v?(\d+\.\d+\.\d+)", ver_str)
                if m:
                    return m.group(1)
            except Exception:
                pass

        # UA: terraform повертає JSON
        if tool["name"] == "terraform":
            try:
                import json
                data = json.loads(result.stdout)
                ver_str = data.get("terraform_version", "")
                if ver_str:
                    return ver_str.lstrip("v")
            except Exception:
                pass

        # UA: Загальний парсинг — шукаємо семантичну версію у виводі
        m = re.search(r"v?(\d+\.\d+\.\d+)", output)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"

def get_latest_version_github(repo: str) -> tuple[str, str] | tuple[None, None]:
    """
    Get latest release version and tag from GitHub API.
    UA: Отримує останню версію з GitHub Releases API.
        v1.4: використовує _get_github_headers() з GITHUB_TOKEN якщо є у .env.
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(url, timeout=10, headers=_get_github_headers())
        resp.raise_for_status()
        data = resp.json()
        tag = data["tag_name"]
        ver = tag.lstrip("v")
        return ver, tag
    except Exception as e:
        log(f"   ⚠️ GitHub API помилка ({repo}): {e}", Colors.YELLOW)
        return None, None

def get_latest_version_github_filtered(
    repo: str, tag_filter: str
) -> tuple[str, str] | tuple[None, None]:
    """
    Get latest release filtered by tag_name substring (case-insensitive).
    UA: Отримує останню версію з GitHub Releases API з фільтрацією по tag_name.
        Використовується для репозиторіїв з кількома продуктами в одному репо
        (наприклад bitwarden/clients: desktop-v*, browser-v*, cli-v*).
        /releases/latest повертає найновіший реліз незалежно від продукту —
        тому потрібна явна фільтрація по списку релізів.
        v1.4: використовує _get_github_headers() з GITHUB_TOKEN якщо є у .env.
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases"
        resp = requests.get(
            url,
            timeout=15,
            headers=_get_github_headers(),
            params={"per_page": 20},
        )
        resp.raise_for_status()
        releases = resp.json()
        for rel in releases:
            # UA: Пропускаємо prerelease та draft
            if rel.get("prerelease") or rel.get("draft"):
                continue
            tag = rel.get("tag_name", "")
            if tag_filter.lower() in tag.lower():
                # UA: Витягуємо версію: "cli-v2026.1.0" → "2026.1.0"
                ver = re.sub(r"^[a-zA-Z\-]+v?", "", tag)
                return ver, tag
    except Exception as e:
        log(f"   ⚠️ GitHub API помилка ({repo}, filter={tag_filter}): {e}",
            Colors.YELLOW)
    return None, None


def get_latest_version_sqlite_org() -> tuple[str, str] | tuple[None, None]:
    """
    Get latest sqlite3 version and download URL from sqlite.org/download.html.
    UA: Парсить sqlite.org/download.html для отримання останньої версії
        sqlite-tools-win-x64-XXXXXXX.zip та URL завантаження.
        Сторінка містить рядок виду: 2026/sqlite-tools-win-x64-3510200.zip
        (не в href — href='hp1.html'; шукаємо YYYY/filename прямо в HTML).
        Версія: числовий рядок XXXXXXX (наприклад 3510200).
    """
    try:
        resp = requests.get(
            "https://www.sqlite.org/download.html",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        html = resp.text
        # UA: Шукаємо YYYY/sqlite-tools-win-x64-XXXXXXX.zip у будь-якому місці HTML
        #     href='hp1.html' — не прямий URL; реальний шлях є в тексті сторінки
        m = re.search(
            r'(20\d{2})/(sqlite-tools-win-x64-([\d]+)\.zip)',
            html
        )
        if m:
            year     = m.group(1)   # e.g. "2026"
            filename = m.group(2)   # e.g. "sqlite-tools-win-x64-3510200.zip"
            ver_str  = m.group(3)   # e.g. "3510200"
            download_url = f"https://www.sqlite.org/{year}/{filename}"
            return ver_str, download_url
    except Exception as e:
        log(f"   ⚠️ sqlite.org помилка: {e}", Colors.YELLOW)
    return None, None


def get_latest_version_k8s() -> str | None:
    """
    Get latest stable kubectl version from Kubernetes CDN.
    UA: Отримує останню стабільну версію kubectl з dl.k8s.io/release/stable.txt.
    """
    try:
        resp = requests.get("https://dl.k8s.io/release/stable.txt", timeout=10)
        resp.raise_for_status()
        return resp.text.strip().lstrip("v")
    except Exception as e:
        log(f"   ⚠️ Kubernetes CDN помилка: {e}", Colors.YELLOW)
        return None

def get_asset_url(repo: str, tag: str, pattern: str) -> str | None:
    """
    Find release asset URL matching pattern.
    UA: Знаходить URL asset-у релізу за regex-патерном.
        v1.4: використовує _get_github_headers() з GITHUB_TOKEN якщо є у .env.
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        resp = requests.get(url, timeout=10, headers=_get_github_headers())
        resp.raise_for_status()
        assets = resp.json().get("assets", [])
        for asset in assets:
            if re.search(pattern, asset["name"]):
                return asset["browser_download_url"]
    except Exception as e:
        log(f"   ⚠️ Помилка отримання assets: {e}", Colors.YELLOW)
    return None

def extract_binary_from_zip(zip_path: str, binary_in_zip: str | None,
                             tool_name: str, target_dir: str) -> bool:
    """
    Extract binary from zip archive to target directory.
    UA: Витягує бінарник з zip-архіву у цільову папку.
        Якщо binary_in_zip=None — шукає {tool_name}.exe у будь-якій підпапці.
        ФІКС v1.1: validate_zip() перед відкриттям.
    """
    # UA: ФІКС v1.1 — перевіряємо ZIP перед розпакуванням
    if not validate_zip(zip_path):
        log(f"   ❌ Файл не є валідним ZIP архівом: {os.path.basename(zip_path)}",
            Colors.RED)
        return False

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            members = z.namelist()

            if binary_in_zip:
                # UA: Точний шлях всередині архіву
                if binary_in_zip in members:
                    target_path = os.path.join(target_dir, os.path.basename(binary_in_zip))
                    with z.open(binary_in_zip) as src, open(target_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    return True
                else:
                    log(f"   ⚠️ {binary_in_zip} не знайдено в архіві", Colors.YELLOW)
                    log(f"   ℹ️  Вміст архіву: {members[:10]}", Colors.CYAN)
                    return False
            else:
                # UA: Шукаємо {tool_name}.exe у будь-якому місці архіву
                exe_name = f"{tool_name}.exe"
                for member in members:
                    if member.endswith(exe_name):
                        target_path = os.path.join(target_dir, exe_name)
                        with z.open(member) as src, open(target_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                        return True
                log(f"   ⚠️ {exe_name} не знайдено в архіві", Colors.YELLOW)
                return False
    except Exception as e:
        log(f"   ❌ Помилка розпакування: {e}", Colors.RED)
        return False

def update_tool(tool: dict) -> None:
    """
    Check and update a single CLI tool.
    UA: Перевіряє та оновлює один CLI інструмент.
    """
    cprint("-" * 50, Colors.BLUE)
    name = tool["name"]
    log(f"🔍 {name.upper()} — {tool['desc']}", Colors.HEADER)

    current_ver = get_installed_version(tool)
    log(f"   ℹ️  Встановлена: {current_ver}", Colors.CYAN)

    # UA: Отримуємо останню версію залежно від джерела
    if tool["source"] == "k8s":
        latest_ver = get_latest_version_k8s()
        if not latest_ver:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
        latest_tag = f"v{latest_ver}"
    elif tool["source"] == "github_filtered":
        # UA: Репозиторій з кількома продуктами — фільтруємо по tag_name
        latest_ver, latest_tag = get_latest_version_github_filtered(
            tool["repo"], tool["tag_filter"]
        )
        if not latest_ver or not latest_tag:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
    elif tool["source"] == "sqlite_org":
        # UA: v1.6 — sqlite.org precompiled binaries (не GitHub)
        latest_ver, sqlite_download_url = get_latest_version_sqlite_org()
        if not latest_ver or not sqlite_download_url:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
        latest_tag = latest_ver  # UA: для sqlite_org tag == version number
    else:
        latest_ver, latest_tag = get_latest_version_github(tool["repo"])
        if not latest_ver or not latest_tag:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return

    log(f"   ℹ️  Остання:     {latest_ver}", Colors.CYAN)

    # UA: Порівняння версій: для sqlite_org використовуємо int (3490100 > 3480000)
    if current_ver != "0.0.0":
        try:
            if tool["source"] == "sqlite_org":
                up_to_date = int(latest_ver) <= int(current_ver)
            else:
                up_to_date = version.parse(latest_ver) <= version.parse(current_ver)
        except Exception:
            up_to_date = False
        if up_to_date:
            log(f"   ✅ {name} актуальний.", Colors.GREEN)
            return

    log(f"   🚀 Оновлення {name} {current_ver} → {latest_ver}...", Colors.HEADER)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(BIN_DIR, exist_ok=True)

    # UA: kubectl — пряме завантаження .exe (без архіву)
    if tool["source"] == "k8s":
        download_url = tool["download_url"].format(version=latest_tag)
        save_path = os.path.join(DOWNLOADS_DIR, f"kubectl-{latest_ver}.exe")
        log(f"   ⬇️  {download_url}", Colors.BLUE)
        try:
            download_file(download_url, save_path, "kubectl")
            target = os.path.join(BIN_DIR, tool["exe"])
            shutil.move(save_path, target)
            log(f"   ✅ kubectl оновлено → {latest_ver}", Colors.GREEN)
        except Exception as e:
            log(f"   ❌ Помилка: {e}", Colors.RED)
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
        return

    # UA: sqlite_org — пряме завантаження zip з sqlite.org (не GitHub API)
    if tool["source"] == "sqlite_org":
        archive_name = sqlite_download_url.split("/")[-1]
        save_path = os.path.join(DOWNLOADS_DIR, archive_name)
        log(f"   ⬇️  {sqlite_download_url}", Colors.BLUE)
        try:
            download_file(sqlite_download_url, save_path, name)
        except Exception as e:
            log(f"   ❌ Помилка завантаження: {e}", Colors.RED)
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
            return
        log(f"   ⚙️  Розпакування {archive_name}...", Colors.BLUE)
        success = extract_binary_from_zip(
            save_path, tool.get("binary_in_zip"), name, BIN_DIR
        )
        try:
            os.remove(save_path)
        except Exception:
            pass
        if success:
            log(f"   ✅ {name} оновлено → {latest_ver}", Colors.GREEN)
        else:
            log(f"   ❌ Не вдалося оновити {name}.", Colors.RED)
        return

    # UA: GitHub — завантажуємо zip-архів
    asset_url = get_asset_url(tool["repo"], latest_tag, tool["asset_pattern"])
    if not asset_url:
        log(f"   ⚠️ Asset не знайдено для {name} {latest_tag}", Colors.YELLOW)
        return

    archive_name = asset_url.split("/")[-1]
    save_path = os.path.join(DOWNLOADS_DIR, archive_name)
    log(f"   ⬇️  {asset_url}", Colors.BLUE)

    try:
        download_file(asset_url, save_path, name)
    except Exception as e:
        log(f"   ❌ Помилка завантаження: {e}", Colors.RED)
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return

    log(f"   ⚙️  Розпакування {archive_name}...", Colors.BLUE)
    success = extract_binary_from_zip(
        save_path,
        tool.get("binary_in_zip"),
        name,
        BIN_DIR
    )

    # UA: Видаляємо архів незалежно від результату
    try:
        os.remove(save_path)
    except Exception:
        pass

    if success:
        new_ver = get_installed_version(tool)
        log(f"   ✅ {name} оновлено → {new_ver}", Colors.GREEN)
    else:
        log(f"   ❌ Не вдалося оновити {name}.", Colors.RED)

# ---------------------------------------------------------------------------
# КРОК 3: Перевірка системного PATH
# ---------------------------------------------------------------------------
def ensure_in_system_path() -> None:
    """Check if apps/bin/ is in system PATH; run fix_path.ps1 via UAC if not.
    UA: Перевіряє наявність apps/bin/ у системному PATH.
        Якщо відсутній — запускає fix_path.ps1 з правами адміністратора.
    """
    cprint("-" * 50, Colors.BLUE)
    log("🔧 ПЕРЕВІРКА СИСТЕМНОГО PATH", Colors.HEADER)

    ps_script = os.path.join(CAPSULE_ROOT, "devops", "pathupdate", "fix_path.ps1")
    if not os.path.exists(ps_script):
        log("   ⚠️ fix_path.ps1 не знайдено, пропускаємо.", Colors.YELLOW)
        return

    try:
        import winreg  # type: ignore[import]
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_READ
        )
        current_path, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
        entries = [e.rstrip('\\').strip() for e in current_path.split(';') if e.strip()]
        bin_norm = BIN_DIR.rstrip('\\')
        if bin_norm in entries:
            log("   ✅ Capsule PATH вже зареєстровано в системі.", Colors.GREEN)
            return
    except Exception:
        pass

    log("   ℹ️  apps/bin/ відсутній в PATH. Запускаю реєстрацію (UAC)...", Colors.YELLOW)
    pwsh = os.path.join(CAPSULE_ROOT, "apps", "pwsh", "pwsh.exe")
    if not os.path.exists(pwsh):
        pwsh = "pwsh"
    try:
        subprocess.run(
            [pwsh, "-NoProfile", "-Command",
             f"Start-Process '{pwsh}' -Verb RunAs -Wait "
             f"-ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{ps_script}\" -AutoClose'"],
            timeout=60,
            stdin=subprocess.DEVNULL,  # UA: ФІКС v1.1 — Input redirection
        )
        log("   ✅ PATH оновлено. Перезапусти термінал для застосування.", Colors.GREEN)
    except Exception as e:
        log(f"   ⚠️ Не вдалося оновити PATH: {e}", Colors.YELLOW)
        log(f"   ℹ️  Запусти вручну: {ps_script}", Colors.CYAN)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    """Main entry point. UA: Головна функція менеджера."""
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\n")
    cprint("=" * 50, Colors.HEADER)
    cprint(f"🚀 MNT: DEVOPS CLI BIN (AUTO-PILOT v{__version__})", Colors.HEADER)
    cprint(f"   Hash: {get_manager_hash()}", Colors.BLUE)
    cprint("=" * 50 + "\n", Colors.HEADER)

    try:
        # UA: Крок 1 — ротація логів
        cleanup_old_logs(days=7, max_size_mb=10.0)

        # UA: Крок 2 — перевірка та оновлення кожного інструменту
        for tool in TOOLS:
            update_tool(tool)

        # UA: Крок 3 — перевірка PATH
        ensure_in_system_path()

    except Exception as e:
        log(f"❌ Критична помилка: {e}", Colors.RED)
        input("Enter для виходу...")
        sys.exit(1)

    elapsed = time.time() - START_TIME
    cprint("-" * 50, Colors.BLUE)
    cprint(f"⏱️  Час виконання: {elapsed:.1f} сек", Colors.BLUE)
    print("\n")

    # UA: Автозакриття через 30 секунд
    if "--install-only" not in sys.argv:
        for i in range(30, 0, -1):
            sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через {i} с... {Colors.RESET}")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через 0 с...  {Colors.RESET}   \n")
        sys.stdout.flush()
        sys.exit(0)

if __name__ == "__main__":
    main()
