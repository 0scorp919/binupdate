@echo off
:: UA: Встановлюємо кодування UTF-8 для коректного відображення кирилиці
chcp 65001 >nul
setlocal

:: ============================================================
:: bin_launcher.bat — Портативний лаунчер DevOps CLI Bin Manager
:: GitHub-ready: auto-detect CAPSULE_ROOT від %~dp0 (без хардкодованих шляхів)
:: Відмінність від tags/bin.lnk: системний лаунчер містить хардкод шляхів
:: і не публікується; bin_launcher.bat — GitHub-ready, лежить поруч з менеджером.
:: ============================================================

:: --- 1. AUTO-DETECT CAPSULE_ROOT ---
:: UA: Структура: devops\binupdate\bin_launcher.bat → два рівні вгору → CAPSULE_ROOT
set "SCRIPT_DIR=%~dp0"
for %%A in ("%SCRIPT_DIR%..\..") do set "CAPSULE_ROOT=%%~fA"

set "PYTHON_EXE=%CAPSULE_ROOT%\apps\python\current\python\python.exe"
set "SCRIPT_FILE=%CAPSULE_ROOT%\devops\binupdate\bin_manager.py"

:: --- 2. ПЕРЕВІРКА ПРАВ АДМІНІСТРАТОРА ---
NET SESSION >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Запит прав адміністратора...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: --- 3. ВІЗУАЛІЗАЦІЯ ---
echo ========================================================
echo   DEVOPS CLI BIN MAINTENANCE (Admin Mode)
echo   (c) Oleksii Rovnianskyi System
echo   Capsule: %CAPSULE_ROOT%
echo ========================================================

:: --- 4. ПЕРЕВІРКИ БЕЗПЕКИ ---
if not exist "%PYTHON_EXE%" (
    echo [CRITICAL ERROR] Python not found at:
    echo "%PYTHON_EXE%"
    echo.
    echo Переконайся що capsule розгорнута коректно.
    pause
    exit /b 1
)

if not exist "%SCRIPT_FILE%" (
    echo [CRITICAL ERROR] Script not found at:
    echo "%SCRIPT_FILE%"
    pause
    exit /b 1
)

:: --- 5. ЗАПУСК СКРИПТА ---
echo [INFO] Запуск Python скрипта...
"%PYTHON_EXE%" "%SCRIPT_FILE%" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Скрипт завершився з помилкою %ERRORLEVEL%.
    echo Перевір лог-файл у logs\binlog\
    pause
) else (
    echo.
    echo [OK] Успішно завершено.
)

endlocal
exit /b 0
