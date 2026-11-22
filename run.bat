@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ds4catia] No se encuentra el entorno virtual .venv en esta carpeta.
    echo Creal con: py -3.12 -m venv .venv
    echo Luego:     .venv\Scripts\activate ^&^& pip install -e .[dev]
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m ds4catia.main %*

if errorlevel 1 (
    echo.
    echo [ds4catia] Termino con un error ^(ver arriba^). Pulsa una tecla para cerrar.
    pause >nul
)
endlocal
