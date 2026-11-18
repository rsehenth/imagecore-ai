"""Calculo de proporcoes e resolucoes."""

import pytest

from backend.core.presets import (
    MAX_SIDE,
    PLATFORM_PRESETS,
    dimensions_for_ratio,
    dimensions_for_tier,
    parse_ratio,
    style_suffix,
)


def test_tiers_por_lado_curto():
    assert dimensions_for_tier("16:9", "1080p") == (1920, 1080)
    assert dimensions_for_tier("9:16", "1080p") == (1080, 1920)
    assert dimensions_for_tier("1:1", "720p") == (720, 720)
    assert dimensions_for_tier("21:9", "720p") == (1680, 720)
    assert dimensions_for_tier("4:3", "2k") == (1920, 1440)
    assert dimensions_for_tier("3:4", "4k") == (2160, 2880)


def test_proporcao_invalida():
    with pytest.raises(ValueError):
        dimensions_for_ratio("16:9:1", 1080)
    with pytest.raises(ValueError):
        parse_ratio("abc")
    with pytest.raises(ValueError):
        parse_ratio("0:5")


def test_presets_de_plataforma_conhecidos():
    keys = {preset["key"] for preset in PLATFORM_PRESETS}
    assert {"youtube_thumb", "youtube_fhd", "youtube_shorts", "instagram_reels",
            "instagram_feed", "instagram_portrait", "instagram_stories", "tiktok"} <= keys
    yt = next(p for p in PLATFORM_PRESETS if p["key"] == "youtube_thumb")
    assert (yt["width"], yt["height"]) == (1280, 720)
    tt = next(p for p in PLATFORM_PRESETS if p["key"] == "tiktok")
    assert (tt["width"], tt["height"]) == (1080, 1920)


def test_estilos_tem_sufixo():
    # Sufixos em ingles (vocabulario do SDXL); o rotulo exibido continua em portugues.
    assert style_suffix("none") == ""
    assert "photorealistic" in style_suffix("photoreal")
    assert style_suffix("desconhecido") == ""


def test_teto_de_lado():
    assert MAX_SIDE == 6272
