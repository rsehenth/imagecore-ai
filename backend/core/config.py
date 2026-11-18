"""Configuracao (YAML + Pydantic v2 + overrides de ambiente).

Adaptado de um projeto anterior do autor (backend/core/config.py), projeto de referencia pessoal;
reduzido as secoes do ImageCore AI (app, storage, upload, generation, provider, comfyui, gpu).
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Erro de configuracao invalida."""


_LOCAL_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AppSettings(BaseModel):
    model_config = {"validate_assignment": True}

    name: str = "ImageCore AI"
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "info"
    # Acesso pela rede local so com opt-in explicito (IMAGECORE_LAN=1); o padrao e loopback.
    allow_lan: bool = False

    @model_validator(mode="after")
    def _validate_host(self) -> "AppSettings":
        host = self.host.strip().lower()
        if host not in _LOCAL_LOOPBACK_HOSTS:
            try:
                ipaddress.ip_address(host)
                is_ip = True
            except ValueError:
                is_ip = False
            if not (self.allow_lan and is_ip):
                raise ValueError(
                    f"host {self.host!r} nao permitido no MVP local; use apenas loopback "
                    "(127.0.0.1, localhost ou ::1) ou habilite IMAGECORE_LAN=1 para a rede local"
                )
        return self


class StorageSettings(BaseModel):
    database_path: Path = Path("data/imagecore.db")


class UploadSettings(BaseModel):
    """Imagens de referencia enviadas pelo usuario (decodificadas em subprocesso isolado)."""

    max_image_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    max_image_side: int = Field(default=8192, ge=64)
    max_image_pixels: int = Field(default=25_000_000, ge=1024)
    processing_timeout_seconds: float = Field(default=15.0, gt=0)
    processing_memory_mb: int = Field(default=1024, ge=128)
    max_references_per_job: int = Field(default=4, ge=0, le=16)
    max_pending: int = Field(default=48, ge=1)


class GenerationSettings(BaseModel):
    max_prompt_chars: int = Field(default=4000, ge=1)
    max_output_pixels: int = Field(default=17_000_000, ge=1024)  # ~17 MP (classe do MiniMax-H3)


class ProviderSettings(BaseModel):
    name: str = "simulated"  # simulated | comfyui

    @field_validator("name")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()


class ComfyUISettings(BaseModel):
    """Adaptador do ComfyUI local. O workflow (API format) e o mapeamento de nos ficam em config/."""

    base_url: str = "http://127.0.0.1:8188"
    workflow: Path = Path("config/workflows/t2i.example.json")
    # Workflow texto->imagem com LoadImage (img2img); ausente = i2i nao suportado.
    workflow_i2i: Path | None = None
    # Raiz de models/ do ComfyUI (Etapa 7): permite detectar honestamente pesos de perfis
    # instalados (ex.: models/diffusion_models/flux-2-klein-4b.safetensors). Vazio = sem
    # verificacao de arquivos (perfis ficam no maximo "compativel").
    models_dir: Path | None = None
    timeout_seconds: float = Field(default=900.0, gt=0)
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    # Estimativa base de VRAM do workflow com SDXL carregado (~1024x1024); NAO e medicao.
    estimated_vram_mb: int = Field(default=8000, ge=0)
    min_free_vram_mb: int = Field(default=1500, ge=0)
    # Negative prompt padrao (ingles — vocabulario do SDXL). Vazio desativa o padrao;
    # o usuario pode sobrescrever (ou anular enviando string vazia) por geracao.
    default_negative_prompt: str = (
        "text, watermark, logo, signature, low quality, worst quality, blurry, jpeg artifacts"
    )
    # Etapa 8.1: declare true SOMENTE se esta instancia do ComfyUI for dedicada ao ImageCore AI
    # (nenhum outro cliente/submetedor a utiliza). Fila vazia nao prova exclusividade; sem esta
    # declaracao, o POST /free (liberacao de VRAM) permanece INDISPONIVEL — nunca descarregaria
    # pesos de outros clientes.
    dedicated: bool = False
    # DEPRECATED (Etapa 8): o /interrupt GLOBAL nao e mais usado em nenhuma circunstancia.
    # O cancelamento individual usa POST /api/jobs/{prompt_id}/cancel (ComfyUI >= 0.45,
    # interrupt atomico por job) e, sem essa API, o comportamento conservador aguarda o fim
    # do prompt proprio. O campo permanece apenas para compatibilidade de config existente.
    interrupt_running: bool = False
    node_map: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "prompt": ["6", "inputs", "text"],
            "negative_prompt": ["7", "inputs", "text"],
            "width": ["5", "inputs", "width"],
            "height": ["5", "inputs", "height"],
            "seed": ["3", "inputs", "seed"],
            "steps": ["3", "inputs", "steps"],
            "cfg": ["3", "inputs", "cfg"],
            "sampler": ["3", "inputs", "sampler_name"],
        }
    )
    # Mapeamento do workflow i2i (o latent vem da imagem; sem width/height; com denoise).
    node_map_i2i: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "prompt": ["6", "inputs", "text"],
            "negative_prompt": ["7", "inputs", "text"],
            "seed": ["3", "inputs", "seed"],
            "denoise": ["3", "inputs", "denoise"],
            "steps": ["3", "inputs", "steps"],
            "cfg": ["3", "inputs", "cfg"],
            "sampler": ["3", "inputs", "sampler_name"],
        }
    )
    # Nos de LoadImage para imagens de referencia (1-based) por workflow. Vazio = nao suportado.
    ref_node_map: dict[int, list[str]] = Field(default_factory=dict)
    ref_node_map_i2i: dict[int, list[str]] = Field(default_factory=dict)


class GpuSettings(BaseModel):
    """Pre-flight de VRAM antes de enfileirar uma geracao real."""

    enabled: bool = True
    # Gerar sem essa folga livre tende a derrubar o backend do ComfyUI com OOM.
    min_free_vram_mb: int = Field(default=1024, ge=0)


class Settings(BaseModel):
    app: AppSettings = AppSettings()
    storage: StorageSettings = StorageSettings()
    upload: UploadSettings = UploadSettings()
    generation: GenerationSettings = GenerationSettings()
    provider: ProviderSettings = ProviderSettings()
    comfyui: ComfyUISettings = ComfyUISettings()
    gpu: GpuSettings = GpuSettings()


def _to_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "IMAGECORE_LAN": ("app", "allow_lan", _to_bool),
    "IMAGECORE_HOST": ("app", "host", str),
    "IMAGECORE_PORT": ("app", "port", int),
    "IMAGECORE_PROVIDER": ("provider", "name", str),
    "IMAGECORE_DB_PATH": ("storage", "database_path", Path),
}


def _load_env_file(root: Path) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _apply_env_overrides(settings: Settings) -> None:
    for env_key, (section, attr, cast) in ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        try:
            setattr(getattr(settings, section), attr, cast(raw))
        except ValueError as exc:
            raise ConfigError(f"Valor invalido para {env_key}: {raw!r} ({exc})") from exc


def _resolve_paths(settings: Settings, root: Path) -> Settings:
    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    settings.storage.database_path = resolve(settings.storage.database_path)
    settings.comfyui.workflow = resolve(settings.comfyui.workflow)
    if settings.comfyui.workflow_i2i is not None:
        settings.comfyui.workflow_i2i = resolve(settings.comfyui.workflow_i2i)
    return settings


def load_settings(config_path: Path | None = None, root: Path | None = None) -> Settings:
    root = root or PROJECT_ROOT
    _load_env_file(root)
    if config_path is None:
        config_path = root / "config" / "settings.yaml"
    data: dict = {}
    if config_path.is_file():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"Configuracao invalida em {config_path}: esperado um mapa YAML")
    try:
        settings = Settings.model_validate(data)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Falha ao carregar configuracao: {exc}") from exc
    _apply_env_overrides(settings)
    return _resolve_paths(settings, root)
