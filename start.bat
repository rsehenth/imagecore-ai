@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  ImageCore AI - inicializacao local (Windows)
rem  - Sobe o motor ComfyUI (porta 8188) e o backend (porta 8765).
rem  - Backend escuta na rede local por padrao (celular no mesmo Wi-Fi);
rem    ENABLE_LAN=0 no inicio do arquivo volta para somente loopback.
rem  - Reutiliza o ComfyUI caso o motor ja esteja ativo na porta 8188.
rem ============================================================

rem 1. variavel de ambiente COMFY_DIR; 2. primeira linha de config\comfy.local.txt
rem (local da maquina, ignorado pelo Git; caminho completo, uma linha, sem aspas);
rem 3. indefinido -> a checagem abaixo mostra como configurar.
if not defined COMFY_DIR if exist "config\comfy.local.txt" set /p COMFY_DIR=<"config\comfy.local.txt"
set "COMFY_URL=http://127.0.0.1:8188/system_stats"
set "BACKEND_URL=http://127.0.0.1:8765/api/health"

rem Acesso pelo Wi-Fi local (celular): 1 = habilitado, 0 = somente loopback.
set "ENABLE_LAN=1"

if not exist ".venv\Scripts\python.exe" (
    echo [ImageCore AI] Virtual environment not found in .venv
    echo            Create it with: python -m venv .venv
    echo            Then:           .venv\Scripts\pip install -e ".[dev]"
    pause
    exit /b 1
)

if not exist "%COMFY_DIR%\run_nvidia_gpu.bat" (
    echo [ImageCore AI] ComfyUI not found %COMFY_DIR:"=%
    echo            Set the COMFY_DIR environment variable, or create config\comfy.local.txt
    echo            with the full ComfyUI path (one line, no quotes) and run again.
    pause
    exit /b 1
)

rem ---------- 1. ComfyUI engine (port 8188) ----------
curl -sf -o NUL --max-time 2 %COMFY_URL%
if not errorlevel 1 (
    echo [ImageCore AI] ComfyUI is already responding at http://127.0.0.1:8188
    goto backend
)
netstat -ano -p TCP 2>nul | findstr /R /C:":8188 .*LISTENING" >nul
if not errorlevel 1 (
    echo [ImageCore AI] Port 8188 is already in use by another process.
    echo            Stop it or adjust comfyui.base_url in config\settings.yaml.
    pause
    exit /b 1
)

echo [ImageCore AI] Starting ComfyUI in its own window...
start "ComfyUI (ImageCore AI)" /D "%COMFY_DIR%" run_nvidia_gpu.bat

set /a tries=0
:wait_comfy
timeout /t 2 /nobreak >nul
curl -sf -o NUL --max-time 2 %COMFY_URL%
if not errorlevel 1 (
    echo [ImageCore AI] ComfyUI is ready at http://127.0.0.1:8188
    goto backend
)
set /a tries+=1
if %tries% lss 60 goto wait_comfy
echo [ImageCore AI] [warning] ComfyUI did not respond within 120 s; continuing without it.
echo            The backend will still start; generation will fail until the engine is up.

:backend
rem ---------- 2. ImageCore AI backend (port 8765) ----------
curl -sf -o NUL --max-time 2 %BACKEND_URL%
if not errorlevel 1 (
    echo [ImageCore AI] A backend is already responding at http://127.0.0.1:8765
    echo            Use that address in the browser; another instance will not be started.
    pause
    exit /b 0
)
netstat -ano -p TCP 2>nul | findstr /R /C:":8765 .*LISTENING" >nul
if not errorlevel 1 (
    echo [ImageCore AI] Port 8765 is already in use by another process.
    echo            Stop the process or change the port in config\settings.yaml.
    pause
    exit /b 1
)

if "%ENABLE_LAN%"=="1" (
    set "IMAGECORE_LAN=1"
    set "IMAGECORE_HOST=0.0.0.0"
)

echo.
echo ============================================================
echo   ImageCore AI starting...
echo.
echo   Interface:  http://127.0.0.1:8765
if "%ENABLE_LAN%"=="1" powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Host ('  Phone/WiFi: http://' + $_.IPAddress + ':8765') }"
echo   Engine:     ComfyUI at http://127.0.0.1:8188 (local only)
echo ============================================================
if "%ENABLE_LAN%"=="1" echo   Open the Phone/WiFi URL on a device in the same network.
if "%ENABLE_LAN%"=="1" echo   If the phone cannot connect, allow Python through Windows Firewall.
echo.

".venv\Scripts\python.exe" -m backend.main
if errorlevel 1 (
    echo [ImageCore AI] The backend exited with an error.
    pause
    exit /b 1
)
