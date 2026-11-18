"""Registry/factory de providers (mesmo padrao de um projeto anterior do autor, backend/providers/registry.py)."""

from __future__ import annotations

from backend.core.config import ConfigError, Settings
from backend.generation.base import ImageProvider
from backend.generation.comfyui import ComfyUIProvider
from backend.generation.simulated import SimulatedProvider

PROVIDERS: dict[str, type[ImageProvider]] = {
    "simulated": SimulatedProvider,
    "comfyui": ComfyUIProvider,
}


def create_provider(settings: Settings) -> ImageProvider:
    provider_name = settings.provider.name
    provider_class = PROVIDERS.get(provider_name)
    if provider_class is None:
        raise ConfigError(
            f"Provider desconhecido: {settings.provider.name!r}. Disponiveis: {', '.join(sorted(PROVIDERS))}"
        )
    if provider_class is ComfyUIProvider:
        return ComfyUIProvider(settings.comfyui)
    return provider_class()
