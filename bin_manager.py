# -*- coding: utf-8 -*-
"""
DevOps CLI Bin Manager v2.5
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
    - Повна відповідність template v3.1: ENABLE_BACKUPS, show_path_info, logging fix

Changelog:
  v2.5 (2026-02-26) — СТАНДАРТ: приведено ensure_in_system_path() до референсу 7zipupdate:
         - show_path_info() тепер викликається всередині ensure_in_system_path()
         - Перевірка PATH через winreg (реєстр) замість `where helm`
         - UAC elevation для fix_path.ps1 через Start-Process -Verb RunAs
         - Порядок main(): PATH → logs → update (як у стандарті)
  v2.4 (2026-02-26) — ФІКС: динамічний таймер автозакриття (зворотний відлік замість статичного "30 секунд")
  v2.3 (2026-02-26) — Аудит: приведення до manager_standard v3.1:
         Додано: ENABLE_BACKUPS=False, manage_backups() (пропуск), show_path_info()
         ФІКС: logging.basicConfig — прибрано StreamHandler (тільки файл)
         Оновлено: __version__ → v2.3
  v2.2 — ФІКС: terraform — новий джерело hc_releases замість github:
         Тепер використовує https://releases.hashicorp.com замість GitHub API
         (HashiCorp більше не публікує архіви на GitHub)
         Додано get_latest_version_hc() для парсингу версій з hashicorp.com
  v2.1 — ФІКС: terraform asset_pattern — додано другий номер версії:
         terraform 1.14.x має assets terraform_1.14.x_... (раніше: terraform_1.14_...)
  v2.0 — Повна відповідність template v3.0:
         Додано: AutoCloseTimer, health_check(), error_reporting(),
         observability_hooks(), check_and_update()
         manage_backups() — НЕ використовується (CLI без user data)
  v1.7 — ФІКС: sqlite3 порівняння версій (bug: завжди оновлювалось):
         sqlite3 --version повертає "3.51.2" (семантична), sqlite.org дає "3510200" (числова).
         Конвертація: X.Y.Z → X*1000000 + Y*10000 + Z*100 перед int-порівнянням.
  v1.6 — Додано sqlite3.exe (SQLite CLI):
         Джерело: github.com/sqlite/sqlite-amalgamation (precompiled binaries)
  v1.5 — Підготовка до публікації на GitHub (аудит портативності):
         CAPSULE_ROOT auto-detect від SCRIPT_DIR
  v1.4 — Стандарт менеджера капсули:
         __version__ + get_manager_hash() (SHA256 self-check)
  v1.3 — Додано Bitwarden CLI (bw.exe)
  v1.2 — Додано GitHub CLI (gh.exe)
  v1.1 — Фікси: stdin=DEVNULL, retry x3, zipfile validation
  v1.0 — Початкова версія.
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
import threading
import signal
from typing import Optional

# ===========================================================================
# VERSION
# ===========================================================================
__version__ = "2.5"

def get_manager_hash() -> str:
    """Return first 12 chars of SHA256 of this script (self-integrity check).
    UA: Перші 12 символів SHA256 власного файлу (self-check цілісності)."""
    try:
        with open(os.path.abspath(__file__), 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception:
        return "????????????"


# ===========================================================================
# AUTO-DETECT CAPSULE ROOT — НЕ хардкодити шляхи!
# UA: SCRIPT_DIR → два рівні вгору → корінь капсули
# Структура: CAPSULE_ROOT/devops/binupdate/bin_manager.py
# ===========================================================================
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CAPSULE_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
APP_NAME     = "bin"


def _load_env() -> dict:
    """Load .env file next to script. UA: Завантаження .env поруч зі скриптом."""
    result: dict = {}
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return result
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


_env = _load_env()

# Шляхи: з .env або auto-detect від CAPSULE_ROOT
LOG_DIR    = _env.get("LOG_DIR")    or os.path.join(CAPSULE_ROOT, "logs",    f"{APP_NAME}log")
BACKUP_DIR = _env.get("BACKUP_DIR") or os.path.join(CAPSULE_ROOT, "backups", APP_NAME)
BIN_DIR    = _env.get("BIN_DIR")    or os.path.join(CAPSULE_ROOT, "apps",    "bin")
SEVEN_ZIP  = _env.get("SEVEN_ZIP")  or os.path.join(CAPSULE_ROOT, "apps",    "7zip", "7za.exe")
DOWNLOADS  = _env.get("DOWNLOADS")  or os.path.join(CAPSULE_ROOT, "downloads")
PWSH_EXE   = _env.get("PWSH_EXE")   or os.path.join(CAPSULE_ROOT, "apps",    "pwsh", "pwsh.exe")

# GitHub token
GITHUB_TOKEN: str | None = _env.get("GITHUB_TOKEN") or None

# Чи потрібні бекапи? CLI-інструменти без даних користувача — вимкнено
ENABLE_BACKUPS: bool = False

START_TIME = time.time()
os.system('')  # enable ANSI colors in Windows CMD


# ===========================================================================
# AUTO-CLOSE TIMER (30 seconds of inactivity)
# ===========================================================================
class AutoCloseTimer:
    """Auto-close after 30 seconds of inactivity.
    UA: Автозакриття після 30 секунд бездіяльності."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.last_activity = time.time()
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def reset(self) -> None:
        """Reset the inactivity timer."""
        self.last_activity = time.time()

    def start(self) -> None:
        """Start the auto-close timer."""
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the auto-close timer."""
        self.running = False

    def _run(self) -> None:
        """Internal timer loop."""
        while self.running:
            if time.time() - self.last_activity > self.timeout:
                cprint(f"\n[{Colors.YELLOW}TIMEOUT{Colors.RESET}] Автозакриття через {self.timeout} сек бездіяльності.", Colors.YELLOW)
                self.running = False
                os._exit(0)
            time.sleep(1)


_auto_close = AutoCloseTimer(30)


# ===========================================================================
# NETWORK TIMEOUTS
# ===========================================================================
DEFAULT_TIMEOUT = 30  # seconds


def network_request_with_retry(url: str, max_retries: int = 3, initial_delay: float = 1.0) -> requests.Response:
    """Make HTTP request with exponential backoff retry.
    UA: HTTP запит з retry та експоненційним backoff."""
    import requests

    delay = initial_delay
    last_error = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                log(f"Спроба {attempt + 1}/{max_retries} невдала: {e}. Повтор через {delay}с...", Colors.YELLOW)
                time.sleep(delay)
                delay *= 2  # exponential backoff

    raise ConnectionError(f"Не вдалося виконати запит після {max_retries} спроб: {last_error}")


# ===========================================================================
# COLORS + CPRINT
# ===========================================================================
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
    """Print colored output to stdout. UA: Кольоровий вивід у консоль."""
    _auto_close.reset()  # Reset auto-close timer on output
    sys.stdout.write(color + msg + Colors.RESET + end)
    sys.stdout.flush()


def draw_progress(label: str, percent: int, width: int = 20) -> None:
    """ASCII progress bar. UA: Прогрес-бар у консолі."""
    _auto_close.reset()
    bars = int(percent / (100 / width))
    bar_viz = '=' * bars + '.' * (width - bars)
    sys.stdout.write(f"\r{Colors.YELLOW}{label}: [{bar_viz}] {percent}%{Colors.RESET}")
    sys.stdout.flush()


# ===========================================================================
# SELF-HEALING DEPENDENCIES
# ===========================================================================
def ensure_dependencies() -> None:
    """Auto-install missing pip packages. UA: Автовстановлення залежностей.
    ONLY for local scripts. NEVER in Docker/CI/CD/production."""
    required: set[str] = {'requests', 'packaging'}
    missing = [lib for lib in required if not _can_import(lib)]
    if missing:
        cprint(f"[SETUP] Installing: {', '.join(missing)}...", Colors.YELLOW)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            cprint(f"[SETUP] Warning: {e}", Colors.YELLOW)


def _can_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


ensure_dependencies()


import requests  # type: ignore
from packaging import version  # type: ignore


# ===========================================================================
# HEALTH CHECKS
# ===========================================================================
def health_check() -> dict:
    """Validate critical components before execution.
    UA: Перевірка критичних компонентів перед виконанням."""
    checks = {
        "bin_dir": os.path.exists(BIN_DIR),
        "log_dir": os.path.exists(LOG_DIR),
        "capsule_root": os.path.exists(CAPSULE_ROOT),
        "downloads_dir": os.path.exists(DOWNLOADS),
    }

    for name, passed in checks.items():
        if not passed:
            log(f"⚠️ {name} не знайдено", Colors.YELLOW)

    return checks


# ===========================================================================
# ERROR REPORTING
# ===========================================================================
def error_reporting(error: Exception, context: str = "") -> None:
    """Structured error handling with actionable messages.
    UA: Структурована обробка помилок з рекомендаціями."""
    error_msg = f"❌ ПОМИЛКА [{context}]: {type(error).__name__}: {error}"
    log(error_msg, Colors.RED)

    if "FileNotFoundError" in str(type(error)):
        log("   ℹ️  Перевірте наявність файлів/директорій", Colors.CYAN)
    elif "PermissionError" in str(type(error)):
        log("   ℹ️  Можливо, потрібні права адміністратора (UAC)", Colors.CYAN)
    elif "ConnectionError" in str(type(error)):
        log("   ℹ️  Перевірте мережеве підключення", Colors.CYAN)

    logging.error(f"{context}: {error}", exc_info=True)


# ===========================================================================
# OBSERVABILITY HOOKS
# ===========================================================================
def observability_hooks() -> dict:
    """Integration points for OpenTelemetry/Prometheus.
    UA: Точки інтеграції для OpenTelemetry/Prometheus."""
    hooks = {
        "metrics": {
            "start_time": START_TIME,
            "app_name": APP_NAME,
            "version": __version__,
            "capsule_root": CAPSULE_ROOT,
        },
        "tracing": {
            "enabled": False,
            "service_name": f"capsule_{APP_NAME}_manager",
            "attributes": {
                "capsule_version": "2.3.x",
                "python_version": sys.version.split()[0],
            }
        }
    }
    return hooks


# ===========================================================================
# LOGGING SETUP
# ===========================================================================
def _rotate_log_if_needed() -> str:
    """If today's log > 50 MB → rename to _part2, _part3... Return active log path.
    UA: Якщо поточний лог > 50 МБ → перейменувати з суфіксом _part2, _part3...
        Повертає шлях до активного лог-файлу. Поточний день ніколи не видаляється."""
    os.makedirs(LOG_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    base = os.path.join(LOG_DIR, f"{APP_NAME}_log_{today}.log")
    if not os.path.exists(base):
        return base
    size_mb = os.path.getsize(base) / (1024 * 1024)
    if size_mb <= 50:
        return base
    part = 2
    while os.path.exists(os.path.join(LOG_DIR, f"{APP_NAME}_log_{today}_part{part}.log")):
        part += 1
    new_path = os.path.join(LOG_DIR, f"{APP_NAME}_log_{today}_part{part}.log")
    os.rename(base, new_path)
    cprint(f"   ♻️  Лог ротовано → {os.path.basename(new_path)}", Colors.YELLOW)
    return base


_log_path = _rotate_log_if_needed()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
    ]
)


def log(msg: str, color: str = Colors.RESET, console: bool = True) -> None:
    """Log to file + optional console. UA: Лог у файл та консоль."""
    logging.info(msg)
    if console:
        cprint(msg, color)


# ===========================================================================
# LOG ROTATION (cleanup old files)
# ===========================================================================
def cleanup_old_logs(max_days: int = 7, max_size_mb: float = 50.0) -> None:
    """Delete log files older than N days. Compress rotated parts to .gz.
    UA: Видаляє лог-файли старші за N днів. Стискає ротовані частини в .gz.
    Поточний день НЕ видаляється."""
    log("🧹 Перевірка старих логів...", Colors.CYAN)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    deleted = 0

    # Part files older than 7 days → compress to .gz
    for f in glob.glob(os.path.join(LOG_DIR, f"{APP_NAME}_log_*_part*.log")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)
        if file_date == today_str:
            continue

        gz_file = f + ".gz"
        if not os.path.exists(gz_file):
            try:
                import gzip
                with open(f, 'rb') as f_in:
                    with gzip.open(gz_file, 'wb') as f_out:
                        f_out.writelines(f_in)
                os.remove(f)
                log(f"   ✓ Стиснуто: {fname} → {fname}.gz", Colors.CYAN)
            except Exception as e:
                log(f"   ⚠️ Помилка стискання {fname}: {e}", Colors.YELLOW)

    # Delete .gz files older than 7 days
    for f in glob.glob(os.path.join(LOG_DIR, f"{APP_NAME}_log_*.log.gz")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)

        try:
            file_date_obj = datetime.datetime.strptime(file_date, "%Y-%m-%d").date()
            days_old = (datetime.date.today() - file_date_obj).days
            if days_old > max_days:
                os.remove(f)
                deleted += 1
        except ValueError:
            continue

    # Delete old part files (not compressed)
    for f in glob.glob(os.path.join(LOG_DIR, f"{APP_NAME}_log_*_part*.log")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)

        try:
            file_date_obj = datetime.datetime.strptime(file_date, "%Y-%m-%d").date()
            days_old = (datetime.date.today() - file_date_obj).days
            if days_old > max_days:
                os.remove(f)
                deleted += 1
        except ValueError:
            continue

    log(f"   ✓ Видалено старих логів: {deleted}", Colors.GREEN)


# ===========================================================================
# BACKUP MANAGEMENT (CLI — skip)
# ===========================================================================
def manage_backups(backup_source: str = None) -> bool:
    """Create AES-256 encrypted backup with rotation (7 daily + 4 weekly).
    UA: Створення зашифрованої резервної копії з ротацією.

    Для CLI-інструментів без даних користувача — пропускається.
    """
    if not ENABLE_BACKUPS:
        log("ℹ️ Бекапи вимкнено (ENABLE_BACKUPS=False). Пропускаю.", Colors.CYAN)
        return True
    log("⚠️ Бекапи увімкнено, але CLI-інструменти не потребують бекапу.", Colors.YELLOW)
    return True


# ===========================================================================
# SHOW PATH INFO
# ===========================================================================
def show_path_info() -> None:
    """Show information about what is registered in PATH for this app.
    UA: Показує інформацію про те, що зареєстровано в PATH для застосунку."""
    cprint("-" * 50, Colors.BLUE)
    log("🔧 ІНФОРМАЦІЯ ПРО PATH", Colors.HEADER)

    tags_dir = os.path.join(CAPSULE_ROOT, "tags")
    bin_dir = BIN_DIR.rstrip('\\')

    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_READ
        )
        current_path, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
        entries = [e.rstrip('\\').strip().lower() for e in current_path.split(';') if e.strip()]
    except Exception:
        entries = []

    tags_norm = tags_dir.rstrip('\\').lower()
    tags_in_path = tags_norm in entries
    bin_norm = bin_dir.lower()
    bin_in_path = bin_norm in entries

    log("", Colors.RESET)
    log("   📋 РЕЄСТРАЦІЯ В PATH:", Colors.CYAN)
    log("", Colors.RESET)

    if tags_in_path:
        log(f"   ✅ tags/         → Win+R → bin (ярлик менеджера)", Colors.GREEN)
    else:
        log(f"   ❌ tags/         → Win+R → bin (ярлик менеджера) — НЕ зареєстровано", Colors.RED)

    if bin_in_path:
        log(f"   ✅ apps/bin/     → helm, kubectl, terraform... (CLI інструменти)", Colors.GREEN)
    else:
        log(f"   ❌ apps/bin/     → helm, kubectl, terraform... — НЕ зареєстровано", Colors.RED)

    log("", Colors.RESET)
    log("   💡 ПРИМІТКА:", Colors.YELLOW)
    log("      Win+R → bin  → запускає менеджер (tags/bin.lnk)", Colors.CYAN)
    log("      Win+R → helm → helm.exe (з apps/bin/)", Colors.CYAN)
    log("", Colors.RESET)


# ===========================================================================
# VERSION CHECK AND UPDATE
# ===========================================================================
def check_and_update(current_version: str = None) -> dict:
    """Check for updates and return update info.
    UA: Перевірка оновлень та повернення інформації про оновлення.

    Returns:
        dict with keys: update_available, latest_version, download_url, notes
    """
    # UA: Це — шаблон. Для binupdate перевірка версій реалізована в update_tool()
    #     Ця функція — для сумісності з template v3.0
    return {
        "update_available": False,
        "latest_version": __version__,
        "download_url": "",
        "notes": "binupdate: перевірка версій реалізована в update_tool()"
    }


# ===========================================================================
# ENSURE IN SYSTEM PATH (UAC-aware)
# ===========================================================================
def ensure_in_system_path() -> None:
    """
    Ensure apps/bin/ is in system PATH (HKLM), remove duplicates.
    UA: Перевіряє що apps/bin/ є в системному PATH (HKLM).
        Якщо відсутній — додає через PowerShell з UAC elevation.
        Також прибирає дублікати та обрізані записи.
        Потрібно для роботи `helm`, `kubectl` та інших CLI з будь-якого місця в системі.
    """
    # UA: Спочатку показуємо інформацію про поточний стан PATH
    show_path_info()

    ps_script = os.path.join(CAPSULE_ROOT, "devops", "pathupdate", "fix_path.ps1")
    if not os.path.exists(ps_script):
        log("   ⚠️ fix_path.ps1 не знайдено, пропускаємо.", Colors.YELLOW)
        return

    # UA: Перевіряємо поточний PATH через реєстр
    try:
        import winreg
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
            # log(f"   ✅ apps/bin/ вже в системному PATH.", Colors.GREEN)
            return
    except Exception:
        pass  # UA: winreg недоступний або помилка читання — продовжуємо

    # UA: apps/bin/ відсутній — запускаємо fix_path.ps1 з UAC
    log(f"   ℹ️  apps/bin/ відсутній в PATH. Запускаю реєстрацію (UAC)...", Colors.YELLOW)
    pwsh = PWSH_EXE if os.path.exists(PWSH_EXE) else "pwsh"

    try:
        subprocess.run(
            [pwsh, "-NoProfile", "-Command",
             f"Start-Process '{pwsh}' -Verb RunAs -Wait "
             f"-ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{ps_script}\" -AutoClose'"],
            timeout=60
        )
        log("   ✅ PATH оновлено. Перезапусти термінал для застосування.", Colors.GREEN)
    except Exception as e:
        log(f"   ⚠️ Не вдалося оновити PATH: {e}", Colors.YELLOW)
        log(f"   ℹ️  Запусти вручну: {ps_script}", Colors.CYAN)


# ===========================================================================
# КОНФІГУРАЦІЯ ІНСТРУМЕНТІВ
# ===========================================================================
TOOLS: list[dict] = [
    {
        "name":    "helm",
        "exe":     "helm.exe",
        "desc":    "Менеджер застосунків Kubernetes.",
        "source":  "github",
        "repo":    "helm/helm",
        "asset_pattern": r"helm-v[\d\.]+-windows-amd64\.zip",
        "binary_in_zip": "windows-amd64/helm.exe",
    },
    {
        "name":    "kubectl",
        "exe":     "kubectl.exe",
        "desc":    "Інтерфейс командного рядка для Kubernetes.",
        "source":  "k8s",
        "version_url":  "https://dl.k8s.io/release/stable.txt",
        "download_url": "https://dl.k8s.io/release/{version}/bin/windows/amd64/kubectl.exe",
    },
    {
        "name":    "terraform",
        "exe":     "terraform.exe",
        "desc":    "Інструмент для інфраструктури як код (IaC).",
        "source":  "hc_releases",
        "version_url": "https://releases.hashicorp.com/terraform/",
        "base_url": "https://releases.hashicorp.com/terraform/{version}/terraform_{version}_windows_amd64.zip",
    },
    {
        "name":    "rclone",
        "exe":     "rclone.exe",
        "desc":    "Синхронізація файлів з хмарними сховищами.",
        "source":  "github",
        "repo":    "rclone/rclone",
        "asset_pattern": r"rclone-v[\d\.]+-windows-amd64\.zip",
        "binary_in_zip": None,
    },
    {
        "name":    "gh",
        "exe":     "gh.exe",
        "desc":    "GitHub CLI.",
        "source":  "github",
        "repo":    "cli/cli",
        "asset_pattern": r"gh_[\d\.]+_windows_amd64\.zip",
        "binary_in_zip": "bin/gh.exe",
    },
    {
        "name":    "bw",
        "exe":     "bw.exe",
        "desc":    "Bitwarden CLI.",
        "source":  "github_filtered",
        "repo":    "bitwarden/clients",
        "tag_filter": "cli",
        "asset_pattern": r"bw-windows-[\d\.]+\.zip",
        "binary_in_zip": "bw.exe",
    },
    {
        "name":    "sqlite3",
        "exe":     "sqlite3.exe",
        "desc":    "SQLite CLI.",
        "source":  "sqlite_org",
        "asset_pattern": r"sqlite-tools-win-x64-[\d]+\.zip",
        "binary_in_zip": None,
    },
]


# ===========================================================================
# УТИЛІТИ ДЛЯ ОНОВЛЕННЯ
# ===========================================================================
def get_installed_version(tool: dict) -> str:
    """Get installed version of a CLI tool."""
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
            stdin=subprocess.DEVNULL,
        )
        output = result.stdout + result.stderr

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

        if tool["name"] == "terraform":
            try:
                import json
                data = json.loads(result.stdout)
                ver_str = data.get("terraform_version", "")
                if ver_str:
                    return ver_str.lstrip("v")
            except Exception:
                pass

        m = re.search(r"v?(\d+\.\d+\.\d+)", output)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def _get_github_headers() -> dict:
    """Build GitHub API headers, adding Authorization if GITHUB_TOKEN is set."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_latest_version_github(repo: str) -> tuple[str, str] | tuple[None, None]:
    """Get latest release version and tag from GitHub API."""
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


def get_latest_version_github_filtered(repo: str, tag_filter: str) -> tuple[str, str] | tuple[None, None]:
    """Get latest release filtered by tag_name substring."""
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
            if rel.get("prerelease") or rel.get("draft"):
                continue
            tag = rel.get("tag_name", "")
            if tag_filter.lower() in tag.lower():
                ver = re.sub(r"^[a-zA-Z\-]+v?", "", tag)
                return ver, tag
    except Exception as e:
        log(f"   ⚠️ GitHub API помилка ({repo}, filter={tag_filter}): {e}", Colors.YELLOW)
    return None, None


def get_latest_version_sqlite_org() -> tuple[str, str] | tuple[None, None]:
    """Get latest sqlite3 version and download URL from sqlite.org."""
    try:
        resp = requests.get(
            "https://www.sqlite.org/download.html",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        html = resp.text
        m = re.search(
            r'(20\d{2})/(sqlite-tools-win-x64-([\d]+)\.zip)',
            html
        )
        if m:
            year     = m.group(1)
            filename = m.group(2)
            ver_str  = m.group(3)
            download_url = f"https://www.sqlite.org/{year}/{filename}"
            return ver_str, download_url
    except Exception as e:
        log(f"   ⚠️ sqlite.org помилка: {e}", Colors.YELLOW)
    return None, None


def get_latest_version_k8s() -> str | None:
    """Get latest stable kubectl version from Kubernetes CDN."""
    try:
        resp = requests.get("https://dl.k8s.io/release/stable.txt", timeout=10)
        resp.raise_for_status()
        return resp.text.strip().lstrip("v")
    except Exception as e:
        log(f"   ⚠️ Kubernetes CDN помилка: {e}", Colors.YELLOW)
        return None


def get_latest_version_hc(product: str) -> tuple[str, str] | tuple[None, None]:
    """Get latest version from HashiCorp releases and construct download URL.
    Returns: (version, download_url) or (None, None)"""
    try:
        url = f"https://releases.hashicorp.com/{product}/"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text
        # Find latest version in the page - look for /product/X.Y.Z format
        m = re.search(rf'/{product}/(\d+\.\d+\.\d+)/"', html)
        if m:
            ver = m.group(1)
            download_url = f"https://releases.hashicorp.com/{product}/{ver}/{product}_{ver}_windows_amd64.zip"
            return ver, download_url
    except Exception as e:
        log(f"   ⚠️ HashiCorp releases помилка ({product}): {e}", Colors.YELLOW)
    return None, None


def get_asset_url(repo: str, tag: str, pattern: str) -> str | None:
    """Find release asset URL matching pattern."""
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


def download_file(url: str, save_path: str, label: str = "Download",
                  retries: int = 3) -> None:
    """Download file with progress bar and retry logic."""
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

                content_type = r.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    raise ValueError(
                        f"Сервер повернув HTML замість файлу "
                        f"(Content-Type: {content_type})."
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
            return

        except Exception as e:
            last_error = e
            if attempt < retries:
                log(f"   ⚠️ Спроба {attempt}/{retries} невдала: {e}. Повтор...", Colors.YELLOW)
                if os.path.exists(save_path):
                    try:
                        os.remove(save_path)
                    except Exception:
                        pass
                time.sleep(2 * attempt)
            else:
                raise RuntimeError(
                    f"Завантаження не вдалося після {retries} спроб: {last_error}"
                ) from last_error


def validate_zip(path: str) -> bool:
    """Validate that file is a valid ZIP archive."""
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 22:
        return False
    return zipfile.is_zipfile(path)


def extract_binary_from_zip(zip_path: str, binary_in_zip: str | None,
                             tool_name: str, target_dir: str) -> bool:
    """Extract binary from zip archive to target directory."""
    if not validate_zip(zip_path):
        log(f"   ❌ Файл не є валідним ZIP архівом: {os.path.basename(zip_path)}", Colors.RED)
        return False

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            members = z.namelist()

            if binary_in_zip:
                if binary_in_zip in members:
                    target_path = os.path.join(target_dir, os.path.basename(binary_in_zip))
                    with z.open(binary_in_zip) as src, open(target_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    return True
                else:
                    log(f"   ⚠️ {binary_in_zip} не знайдено в архіві", Colors.YELLOW)
                    return False
            else:
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
    """Check and update a single CLI tool."""
    cprint("-" * 50, Colors.BLUE)
    name = tool["name"]
    log(f"🔍 {name.upper()} — {tool['desc']}", Colors.HEADER)

    current_ver = get_installed_version(tool)
    log(f"   ℹ️  Встановлена: {current_ver}", Colors.CYAN)

    if tool["source"] == "k8s":
        latest_ver = get_latest_version_k8s()
        if not latest_ver:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
        latest_tag = f"v{latest_ver}"
    elif tool["source"] == "hc_releases":
        latest_ver, hc_download_url = get_latest_version_hc(tool["name"])
        if not latest_ver or not hc_download_url:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
        latest_tag = latest_ver
    elif tool["source"] == "github_filtered":
        latest_ver, latest_tag = get_latest_version_github_filtered(
            tool["repo"], tool["tag_filter"]
        )
        if not latest_ver or not latest_tag:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
    elif tool["source"] == "sqlite_org":
        latest_ver, sqlite_download_url = get_latest_version_sqlite_org()
        if not latest_ver or not sqlite_download_url:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return
        latest_tag = latest_ver
    else:
        latest_ver, latest_tag = get_latest_version_github(tool["repo"])
        if not latest_ver or not latest_tag:
            log(f"   ⚠️ Не вдалося отримати версію {name}.", Colors.YELLOW)
            return

    log(f"   ℹ️  Остання:     {latest_ver}", Colors.CYAN)

    if current_ver != "0.0.0":
        try:
            if tool["source"] == "sqlite_org":
                if "." in current_ver:
                    parts = current_ver.split(".")
                    cur_int = (int(parts[0]) * 1_000_000
                               + int(parts[1]) * 10_000
                               + int(parts[2]) * 100)
                else:
                    cur_int = int(current_ver)
                up_to_date = int(latest_ver) <= cur_int
            else:
                up_to_date = version.parse(latest_ver) <= version.parse(current_ver)
        except Exception:
            up_to_date = False
        if up_to_date:
            log(f"   ✅ {name} актуальний.", Colors.GREEN)
            return

    log(f"   🚀 Оновлення {name} {current_ver} → {latest_ver}...", Colors.HEADER)
    os.makedirs(DOWNLOADS, exist_ok=True)
    os.makedirs(BIN_DIR, exist_ok=True)

    if tool["source"] == "k8s":
        download_url = tool["download_url"].format(version=latest_tag)
        save_path = os.path.join(DOWNLOADS, f"kubectl-{latest_ver}.exe")
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

    if tool["source"] == "sqlite_org":
        archive_name = sqlite_download_url.split("/")[-1]
        save_path = os.path.join(DOWNLOADS, archive_name)
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

    if tool["source"] == "hc_releases":
        # HashiCorp: direct download URL already constructed
        archive_name = hc_download_url.split("/")[-1]
        save_path = os.path.join(DOWNLOADS, archive_name)
        log(f"   ⬇️  {hc_download_url}", Colors.BLUE)
        try:
            download_file(hc_download_url, save_path, name)
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

    asset_url = get_asset_url(tool["repo"], latest_tag, tool["asset_pattern"])
    if not asset_url:
        log(f"   ⚠️ Asset не знайдено для {name} {latest_tag}", Colors.YELLOW)
        return

    archive_name = asset_url.split("/")[-1]
    save_path = os.path.join(DOWNLOADS, archive_name)
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

    try:
        os.remove(save_path)
    except Exception:
        pass

    if success:
        new_ver = get_installed_version(tool)
        log(f"   ✅ {name} оновлено → {new_ver}", Colors.GREEN)
    else:
        log(f"   ❌ Не вдалося оновити {name}.", Colors.RED)


# ===========================================================================
# MAIN ENTRY POINT
# ===========================================================================
def main() -> int:
    """Main entry point. UA: Головна функція менеджера."""
    global APP_NAME

    os.system('cls' if os.name == 'nt' else 'clear')
    print("\n")
    cprint("=" * 50, Colors.HEADER)
    cprint(f"🚀 MNT: DEVOPS CLI BIN (AUTO-PILOT v{__version__})", Colors.HEADER)
    cprint(f"   Hash: {get_manager_hash()}", Colors.BLUE)
    cprint("=" * 50 + "\n", Colors.HEADER)

    # Start auto-close timer
    _auto_close.start()

    try:
        # UA: Крок 1 — перевірка PATH (до будь-яких мережевих операцій)
        ensure_in_system_path()

        # Health check
        checks = health_check()
        if not all(checks.values()):
            log("⚠️ Деякі перевірки не пройдено", Colors.YELLOW)

        # Log observability hooks
        hooks = observability_hooks()
        log(f"   ℹ️  Observability: {hooks['tracing']['service_name']}", Colors.CYAN)

        # UA: Крок 2 — ротація логів (7 днів; >50 MB → part-файл)
        cleanup_old_logs(max_days=7, max_size_mb=10.0)

        # UA: Крок 3 — перевірка та оновлення інструментів
        for tool in TOOLS:
            update_tool(tool)

        log("Готово!", Colors.GREEN)

        # Auto-close after completion — динамічний зворотний відлік
        _auto_close.stop()
        for i in range(30, 0, -1):
            sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через {i} с... {Colors.RESET}")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через 0 с...  {Colors.RESET}   \n")
        sys.stdout.flush()
        return 0

    except KeyboardInterrupt:
        log("\n⚠️ Перервано користувачем", Colors.YELLOW)
        return 1
    except Exception as e:
        error_reporting(e, "main")
        return 1
    finally:
        elapsed = time.time() - START_TIME
        cprint(f"⏱️  Час виконання: {elapsed:.1f} сек", Colors.BLUE)


if __name__ == "__main__":
    sys.exit(main())
