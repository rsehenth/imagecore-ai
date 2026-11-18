"""Presets de plataformas, proporcoes e calculo de resolucoes (grade de 32 px)."""

from __future__ import annotations

import re

from backend.core.errors import ValidationError

# Presets de redes sociais (briefing, secao 4). Dimensoes finais de exportacao.
PLATFORM_PRESETS: list[dict] = [
    {"key": "youtube_thumb", "platform": "YouTube", "label": "Thumbnail", "ratio": "16:9", "width": 1280, "height": 720},
    {"key": "youtube_fhd", "platform": "YouTube", "label": "Full HD", "ratio": "16:9", "width": 1920, "height": 1080},
    {"key": "youtube_shorts", "platform": "YouTube", "label": "Shorts", "ratio": "9:16", "width": 1080, "height": 1920},
    {"key": "instagram_reels", "platform": "Instagram", "label": "Reels", "ratio": "9:16", "width": 1080, "height": 1920},
    {"key": "instagram_feed", "platform": "Instagram", "label": "Feed", "ratio": "1:1", "width": 1080, "height": 1080},
    {"key": "instagram_portrait", "platform": "Instagram", "label": "Retrato", "ratio": "4:5", "width": 1080, "height": 1350},
    {"key": "instagram_stories", "platform": "Instagram", "label": "Stories", "ratio": "9:16", "width": 1080, "height": 1920},
    {"key": "tiktok", "platform": "TikTok", "label": "TikTok", "ratio": "9:16", "width": 1080, "height": 1920},
]

ASPECT_RATIOS: list[dict] = [
    {"key": "1:1"}, {"key": "16:9"}, {"key": "9:16"}, {"key": "4:3"},
    {"key": "3:4"}, {"key": "3:2"}, {"key": "2:3"}, {"key": "21:9"},
]

# Tiers por lado curto (16:9 1080p -> 1920x1080; 9:16 1080p -> 1080x1920; 2K = QHD 1440).
RESOLUTION_TIERS: list[dict] = [
    {"key": "720p", "short_side": 720},
    {"key": "1080p", "short_side": 1080},
    {"key": "2k", "short_side": 1440},
    {"key": "4k", "short_side": 2160},
]

# Sufixos de estilo acrescentados ao prompt (visiveis na UI e salvos no historico).
# Em INGLES de proposito: o SDXL Base foi treinado com captions em ingles, e sufixos
# em portugues degradam a aderencia (o rotulo exibido ao usuario continua em pt-BR).
STYLE_SUFFIXES: dict[str, str] = {
    "none": "",
    "photoreal": ", photorealistic, natural lighting, sharp focus, high detail",
    "illustration": ", digital illustration, vibrant colors, clean line art",
    "render3d": ", 3D render, realistic materials, studio lighting",
    "anime": ", anime style, line art, flat colors",
    "minimal": ", minimalist design, simple shapes, clean background",
    "cinematic": ", cinematic, high contrast, film color grading",
}

STYLE_LABELS: dict[str, str] = {
    "none": "Nenhum (so o prompt)",
    "photoreal": "Fotorrealista",
    "illustration": "Ilustracao digital",
    "render3d": "Render 3D",
    "anime": "Anime",
    "minimal": "Minimalista",
    "cinematic": "Cinematografico",
}

_RATIO_RE = re.compile(r"^(\d{1,2}):(\d{1,2})$")
MAX_SIDE = 6272  # classe 16 MP (MiniMax-H3); teto para dimensoes personalizadas

# Buckets nativos do SDXL (~1 MP, multiplos de 64) — geracao acontece aqui; dimensoes
# finais exatas (preset de plataforma/tier) sao alcancadas por redimensionamento na exportacao.
SDXL_NATIVE_BUCKETS: list[tuple[int, int]] = [
    (1024, 1024),
    (1152, 896),
    (1216, 832),
    (1344, 768),
    (1536, 640),
    (896, 1152),
    (832, 1216),
    (768, 1344),
    (640, 1536),
]

# Orcamento de pixels do latent img2img: a referencia e reescala para ca antes do VAE
# (uma foto de 25 MP codificada direta estouraria a VRAM).
I2I_PIXEL_BUDGET = 1024 * 1024
_I2I_GRAIN = 16
_I2I_MIN_SIDE = 256

# Presets recomendados (quality) e opcao avancada. Nao sao medicoes: sao pontos de partida.
QUALITY_PRESETS: dict[str, dict] = {
    "rapida": {"steps": 20, "cfg": 6.5},
    "padrao": {"steps": 30, "cfg": 7.0},
    "alta": {"steps": 40, "cfg": 7.5},
}
QUALITY_LABELS: dict[str, str] = {
    "rapida": "Rápida (20 passos)",
    "padrao": "Padrão (30 passos)",
    "alta": "Alta qualidade (40 passos)",
}
SAMPLERS: tuple[str, ...] = (
    "euler", "euler_ancestral", "heun", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m_sde", "ddim", "uni_pc",
)
STEPS_RANGE = (8, 60)
CFG_RANGE = (1.0, 15.0)
DENOISE_RANGE = (0.1, 0.95)
I2I_DEFAULT_DENOISE = 0.6


def sdxl_bucket_for(width: int, height: int) -> tuple[int, int]:
    """Bucket nativo SDXL com proporcao mais proxima das dimensoes pedidas (desempate por area)."""
    target = width / height
    return min(
        SDXL_NATIVE_BUCKETS,
        key=lambda bucket: (abs(bucket[0] / bucket[1] - target), abs(bucket[0] * bucket[1] - width * height)),
    )


def i2i_latent_dims(width: int, height: int) -> tuple[int, int]:
    """Dimensoes seguras para codificar a referencia como latent (reescala ate o orcamento, grade de 16 px)."""
    scale = min(1.0, (I2I_PIXEL_BUDGET / max(1, width * height)) ** 0.5)
    def snap(value: float) -> int:
        return max(_I2I_MIN_SIDE, int(round(value / _I2I_GRAIN)) * _I2I_GRAIN)
    return snap(width * scale), snap(height * scale)


def parse_ratio(ratio: str) -> tuple[int, int]:
    match = _RATIO_RE.match(ratio.strip())
    if not match:
        raise ValidationError(f"Proporcao invalida: {ratio!r}", code="unknown_ratio", details={"value": ratio})
    w, h = int(match.group(1)), int(match.group(2))
    if w < 1 or h < 1:
        raise ValidationError(f"Proporcao invalida: {ratio!r}", code="unknown_ratio", details={"value": ratio})
    return w, h


def dimensions_for_ratio(ratio: str, short_side: int) -> tuple[int, int]:
    """Dimensoes reais de uma proporcao com o lado curto dado (ex.: 16:9 1080p -> 1920x1080).

    Nota: workflows que exigem grade de 32 px (MiniMax-H3) devem receber dimensoes
    alinhadas; isso fica a cargo do workflow/adapter, nao dos presets de plataforma."""
    rw, rh = parse_ratio(ratio)
    if rw >= rh:
        return round(short_side * rw / rh), short_side
    return short_side, round(short_side * rh / rw)


def dimensions_for_tier(ratio: str, tier: str) -> tuple[int, int]:
    tier_key = tier.strip().lower()
    for entry in RESOLUTION_TIERS:
        if entry["key"] == tier_key:
            return dimensions_for_ratio(ratio, entry["short_side"])
    raise ValidationError(f"Resolucao desconhecida: {tier!r}", code="unknown_resolution", details={"value": tier})


def tier_for_dimensions(width: int, height: int) -> str:
    """Rotulo do tier mais proximo das dimensoes dadas (para exibicao no historico)."""
    short = min(width, height)
    best = RESOLUTION_TIERS[0]["key"]
    best_delta = abs(short - RESOLUTION_TIERS[0]["short_side"])
    for entry in RESOLUTION_TIERS[1:]:
        delta = abs(short - entry["short_side"])
        if delta < best_delta:
            best, best_delta = entry["key"], delta
    return best


def style_suffix(style: str | None) -> str:
    if not style:
        return ""
    return STYLE_SUFFIXES.get(style.strip().lower(), "")
