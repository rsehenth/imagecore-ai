# ImageCore AI - encerra SOMENTE os proprios processos: janela do start.bat,
# backend e processos ComfyUI Windows Portable identificados pelo caminho.
# Use -Simular para apenas listar o que seria fechado.
# Mensagens exibidas ao usuario sao em ingles (padrao do projeto).
param([switch]$Simular)
$ErrorActionPreference = 'SilentlyContinue'

function Encerrar($id, $label) {
    Write-Host ("  {0,-10} PID {1}" -f $label, $id)
    if (-not $Simular) { taskkill /PID $id /T /F | Out-Null }
}

function PortaAberta($ip, $port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return $c.ConnectAsync($ip, $port).Wait(700) -and $c.Connected } catch { return $false } finally { $c.Close() }
}

$procs = Get-CimInstance Win32_Process

Write-Host "  checking http://127.0.0.1:8765 (backend)"
foreach ($p in $procs) {
    if ($p.Name -eq 'cmd.exe' -and $p.CommandLine -match 'ImageCore AI\\start\.bat') { Encerrar $p.ProcessId 'start.bat' }
}
foreach ($p in $procs) {
    # Etapa 7: o nome do projeto pode nao estar na linha de comando (launch relativo,
    # ex.: ".venv\Scripts\python.exe -m backend.main"); o ExecutablePath resolvido sempre
    # tem o caminho completo — filtro seguro que nunca toca no venv de outros projetos.
    if ($p.Name -match '^pythonw?\.exe$' -and $p.CommandLine -match 'backend\.main' -and $p.ExecutablePath -match 'ImageCore AI') { Encerrar $p.ProcessId 'backend' }
}

Write-Host "  checking http://127.0.0.1:8188 (ComfyUI)"
foreach ($p in $procs) {
    # Janela aberta pelo start.bat (start /D ComfyUI_windows_portable run_nvidia_gpu.bat)
    # ou lancamento manual pelo proprio .bat. taskkill /T leva o python embutido junto.
    if ($p.Name -eq 'cmd.exe' -and $p.CommandLine -match 'ComfyUI_windows_portable\\run_nvidia_gpu\.bat') { Encerrar $p.ProcessId 'comfyui cmd' }
}
foreach ($p in $procs) {
    # Python embutido do portable; o filtro pelo ExecutablePath nunca toca em
    # outros pythons (venv do ImageCore, Ollama, etc.).
    if ($p.Name -match '^pythonw?\.exe$' -and $p.CommandLine -match 'ComfyUI\\main\.py' -and $p.ExecutablePath -match 'ComfyUI_windows_portable') { Encerrar $p.ProcessId 'comfyui' }
}

if ($Simular) { Write-Host '  (dry run: nothing was stopped)'; return }

Start-Sleep -Seconds 1
if (PortaAberta '127.0.0.1' 8765) {
    Write-Host '  [warning] http://127.0.0.1:8765 is still responding'
} else {
    Write-Host '  port 8765 closed'
}
if (PortaAberta '127.0.0.1' 8188) {
    Write-Host '  [warning] http://127.0.0.1:8188 is still responding'
} else {
    Write-Host '  port 8188 closed'
}
