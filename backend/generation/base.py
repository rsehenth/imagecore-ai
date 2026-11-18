"""Contrato dos motores de geracao de imagem.

Padrao provider (ABC + registry + factory por configuracao) derivado de um projeto anterior do autor
(backend/providers/base.py), projeto de referencia pessoal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class GenerationError(Exception):
    """O motor respondeu com erro (workflow invalido, falha de amostragem etc.).

    `code` (opcional): identificador estavel e independente de idioma para a interface
    traduzir a mensagem (frontend/js/locales, mapeamento em frontend/js/i18n.js); a mensagem
    tecnica original segue preservada para diagnostico e serve de fallback na UI."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class GenerationUnavailableError(GenerationError):
    """Motor inacessivel (sem conexao, timeout)."""


class VramInsufficientError(GenerationError):
    """VRAM livre insuficiente para o workflow configurado."""


class GenerationCancelled(GenerationError):
    """Geracao cancelada a pedido do usuario."""


@dataclass
class GenerateRequest:
    job_id: str
    prompt: str
    width: int
    height: int
    seed: int
    style: str = "none"
    references: list[Path] = field(default_factory=list)
    mode: str = "t2i"  # t2i = texto para imagem | i2i = imagem para imagem (1 referencia)
    denoise: float | None = None  # i2i: intensidade da transformacao
    steps: int | None = None
    cfg: float | None = None
    sampler: str | None = None
    negative_prompt: str | None = None  # None = padrao do motor; "" = sem negative
    # Etapa 7 (perfis): id do perfil e modelo que o workflow DEVE carregar. O provider
    # recusa execucao quando o workflow configurado aponta para outro modelo (nunca ha
    # substituicao silenciosa entre o momento do pedido e o momento da execucao).
    profile: str = ""
    expected_model: str = ""


@dataclass
class GenerateResult:
    data: bytes
    mime: str
    ext: str
    width: int
    height: int
    note: str = ""  # observacao honesta exibida no historico (ex.: "simulado", "upscaled")


ProgressFn = Callable[[str, int], None]
CancelCheck = Callable[[], bool]


class ImageProvider(ABC):
    """Contrato para motores de geracao local (simulado hoje; ComfyUI e outros no futuro)."""

    name: str = "base"
    # VRAM estimada do workflow carregado (referencia do pre-flight); 0 = nao usa GPU.
    required_vram_mb: int = 0

    def estimate_vram_mb(self, width: int, height: int, mode: str = "t2i", comfyui_vram_mb: int | None = None) -> int:
        """Estimativa (NAO medicao) de VRAM para a resolucao/modo pedidos; refina o pre-flight.

        `comfyui_vram_mb` e a memoria de GPU usada pelo PROCESSO do proprio motor
        (evidencia do worker); uso global da GPU de outros aplicativos nunca indica
        pesos residentes."""
        return self.required_vram_mb

    @abstractmethod
    async def generate(
        self, request: GenerateRequest, *, progress: ProgressFn, cancel: CancelCheck
    ) -> GenerateResult:
        """Gera uma imagem. `progress(stage, 0-100)` reporta etapas; `cancel()` e checado em pontos seguros."""

    @abstractmethod
    async def health(self) -> dict:
        """Verifica a disponibilidade do motor; levanta GenerationUnavailableError quando fora do ar."""

    @abstractmethod
    def capabilities(self) -> dict:
        """Capacidades reais anunciadas a interface (referencias, edicao, notas)."""

    async def cancel_current(self, job_id: str | None = None) -> None:
        """Melhor esforco para interromper a geracao em curso (job identificado quando possivel)."""

    async def aclose(self) -> None:
        """Libera recursos do provider."""
