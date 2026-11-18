"""Snapshot de recursos de GPU via nvidia-smi (opcional: ausencia degrada para None).

Copiado de um projeto anterior do autor (backend/core/gpu.py), projeto de referencia pessoal.

Etapa 4: diagnostico separado de VRAM total/livre/usada + processos da GPU
(--query-compute-apps) para distinguir a memoria do ComfyUI da memoria de outros
aplicativos. O PID do ComfyUI e identificado pela porta que escuta (netstat); nada
aqui encerra ou interfere em processos externos — apenas leitura.
"""

import shutil
import subprocess
from urllib.parse import urlsplit

_QUERY = "name,memory.used,memory.total,memory.free,utilization.gpu,temperature.gpu"
_QUERY_PROCESSES = "pid,process_name,used_memory"


def _to_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_nvidia_smi(output: str) -> list[dict]:
    gpus: list[dict] = []
    for line in output.strip().splitlines():
        values = [v.strip() for v in line.split(",")]
        if len(values) < 5:
            continue
        gpus.append(
            {
                "name": values[0],
                "memory_used_mb": _to_int(values[1]),
                "memory_total_mb": _to_int(values[2]),
                "memory_free_mb": _to_int(values[3]),
                "utilization_gpu_percent": _to_int(values[4]),
                "temperature_c": _to_int(values[5]) if len(values) > 5 else None,
            }
        )
    return gpus


def parse_process_apps(output: str) -> list[dict]:
    """Linhas 'pid, process_name, used_memory' do --query-compute-apps (WDDM pode nao listar)."""
    processes: list[dict] = []
    for line in output.strip().splitlines():
        values = [v.strip() for v in line.split(",")]
        if len(values) < 3:
            continue
        pid = _to_int(values[0])
        if pid is None:
            continue
        processes.append({"pid": pid, "process_name": values[1], "used_memory_mb": _to_int(values[2])})
    return processes


def gpu_snapshot() -> list[dict] | None:
    """Consulta nvidia-smi; retorna None quando a ferramenta nao esta disponivel."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_nvidia_smi(result.stdout)


def gpu_processes() -> list[dict] | None:
    """Processos com alocacao de memoria na GPU (pid, nome, MB). None = indisponivel.

    Em drivers WDDM a lista pode vir vazia mesmo com processos ativos: o chamador deve
    tratar vazio como 'nao identificado', nunca como 'sem uso'."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, f"--query-compute-apps={_QUERY_PROCESSES}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_process_apps(result.stdout)


def _netstat_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for protocol in ("TCP", "TCPv6"):
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", protocol],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0].upper() == "TCP":
                rows.append(fields)
    return rows


def _local_port(local_address: str, port: int) -> bool:
    return local_address.rsplit(":", 1)[-1] == str(port)


def pid_listening_on_port(port: int) -> int | None:
    """PID do processo escutando a porta TCP informada (netstat v4+v6; leitura, sem acao).

    Quando a tabela nao traz LISTENING para a porta (bind exclusivamente v6 visivel so
    na tabela TCPv6, ou filtro do sistema), usa o dono das conexoes cujo endereco LOCAL
    tem a porta — no ComfyUI em loopback isso identifica o servidor, nao os clientes."""
    rows = _netstat_rows()
    listeners = [
        int(fields[4]) for fields in rows
        if fields[3].upper() == "LISTENING" and _local_port(fields[1], port)
        and fields[4].isdigit() and int(fields[4]) > 0
    ]
    if listeners:
        return listeners[0]
    owners = {
        int(fields[4]) for fields in rows
        if _local_port(fields[1], port) and fields[4].isdigit() and int(fields[4]) > 0
    }
    return next(iter(owners), None)


def port_of_base_url(base_url: str) -> int | None:
    try:
        port = urlsplit(base_url.strip()).port
    except ValueError:
        return None
    return port


def gpu_evidence(base_url: str | None) -> dict:
    """Evidencia de VRAM do proprio motor (ComfyUI) na GPU — leitura passiva.

    Retorna {'processes', 'comfyui_pid', 'comfyui_vram_mb', 'note'}; valores None
    significam 'nao foi possivel identificar' (nunca devem ser lidos como 0)."""
    evidence: dict = {"processes": None, "comfyui_pid": None, "comfyui_vram_mb": None, "note": ""}
    port = port_of_base_url(base_url) if base_url else None
    processes = gpu_processes()
    evidence["processes"] = processes
    if processes is None:
        evidence["note"] = "nvidia-smi indisponivel: memoria por processo nao identificada."
        return evidence
    if not processes:
        evidence["note"] = "driver nao informou processos de GPU (WDDM): memoria do motor nao identificada."
        return evidence
    if port is None:
        evidence["note"] = "porta do motor indefinida na configuracao: memoria do motor nao identificada."
        return evidence
    pid = pid_listening_on_port(port)
    if pid is None:
        evidence["note"] = f"nenhum processo escutando a porta {port}: motor nao identificado."
        return evidence
    evidence["comfyui_pid"] = pid
    entries = [p for p in processes if p.get("pid") == pid]
    if not entries:
        evidence["note"] = f"processo {pid} escuta a porta {port}, mas nao aparece na lista de memoria de GPU."
        return evidence
    used_values = [p.get("used_memory_mb") for p in entries]
    if any(value is None for value in used_values):
        # Drivers WDDM (Windows) frequentemente reportam "[N/A]" por processo: nunca
        # inventar 0 MB — a residencia dos pesos simplesmente nao pode ser confirmada.
        evidence["note"] = (
            f"processo {pid} identificado na porta {port}, mas o driver nao informa a memoria "
            "de GPU por processo (WDDM): residencia dos pesos nao confirmada."
        )
        return evidence
    evidence["comfyui_vram_mb"] = sum(value or 0 for value in used_values)
    evidence["note"] = f"motor (pid {pid}) usando cerca de {evidence['comfyui_vram_mb']} MB de VRAM (leitura do driver)."
    return evidence
