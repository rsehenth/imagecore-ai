"""Provider simulado: placeholder deterministico gerado com Pillow (SEM inferencia de IA).

Serve para desenvolver e validar todo o fluxo (jobs, galeria, exportacao, camada de texto) sem
pesos de modelos instalados. A imagem e rotulada "SIMULACAO - SEM IA" e a UI repete o aviso.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.core.presets import QUALITY_PRESETS
from backend.generation.base import (
    CancelCheck,
    GenerateRequest,
    GenerateResult,
    GenerationCancelled,
    GenerationError,
    ImageProvider,
    ProgressFn,
)

_MAX_LABEL_CHARS = 220


def _hsl(h: int, s: float, l: float) -> tuple[int, int, int]:
    import colorsys

    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


class SimulatedProvider(ImageProvider):
    name = "simulated"
    required_vram_mb = 0

    async def generate(
        self, request: GenerateRequest, *, progress: ProgressFn, cancel: CancelCheck
    ) -> GenerateResult:
        # Etapa 7 (perfis): o placeholder nunca substitui silenciosamente outro modelo.
        if request.profile and request.profile != "sdxl":
            raise GenerationError(
                f"Provider simulado so atende o perfil 'sdxl'; pedido do perfil '{request.profile}' "
                "recusado (sem substituicao silenciosa por simulacao)."
            )
        for pct in range(0, 101, 10):
            if cancel():
                raise GenerationCancelled("Geracao cancelada pelo usuario.")
            stage = "compondo" if pct < 40 else "renderizando"
            progress(stage, max(1, pct))
            await asyncio.sleep(0.06)
        data = await asyncio.to_thread(self._draw, request)
        return GenerateResult(
            data=data,
            mime="image/png",
            ext="png",
            width=request.width,
            height=request.height,
            note="Simulacao local (sem IA): substitua o provider por um motor real para gerar de verdade.",
        )

    async def health(self) -> dict:
        return {"ok": True, "provider": self.name, "mode": "placeholder sem IA"}

    def capabilities(self) -> dict:
        return {
            "references": True,
            "img2img": True,
            "max_references": 4,
            "native_profiles": False,  # placeholder nao modela perfis nativos de resolucao
            "quality_presets": sorted(QUALITY_PRESETS.keys()),
            "editable": False,
            "note": "Provider simulado: valida o fluxo ponta a ponta sem modelos instalados.",
        }

    # ------------------------------------------------------------------ desenho

    def _draw(self, request: GenerateRequest) -> bytes:
        digest = hashlib.sha256(
            f"{request.prompt}|{request.seed}|{request.width}x{request.height}".encode("utf-8")
        ).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        hue = int.from_bytes(digest[8:10], "big") % 360

        width, height = request.width, request.height
        img = Image.new("RGB", (width, height), _hsl(hue, 0.35, 0.16))
        draw = ImageDraw.Draw(img, "RGBA")

        # gradiente vertical suave
        top = _hsl(hue, 0.45, 0.24)
        bottom = _hsl((hue + 40) % 360, 0.40, 0.10)
        for y in range(height):
            t = y / max(1, height - 1)
            color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            draw.line([(0, y), (width, y)], fill=color)

        # blobs translucidos "composicao"
        for _ in range(rng.randint(5, 9)):
            radius = rng.randint(min(width, height) // 8, min(width, height) // 3)
            x, y = rng.randint(0, width), rng.randint(0, height)
            blob_hue = (hue + rng.randint(-70, 70)) % 360
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=_hsl(blob_hue, 0.5, 0.4) + (rng.randint(24, 64),),
            )

        # linhas metalicas discretas
        for _ in range(rng.randint(2, 4)):
            x1, y1 = rng.randint(0, width), rng.randint(0, height)
            x2, y2 = rng.randint(0, width), rng.randint(0, height)
            draw.line([(x1, y1), (x2, y2)], fill=(150, 170, 190, 40), width=max(1, min(width, height) // 400))

        # referencias misturadas (valida o caminho de multiplas referencias no fluxo)
        for index, ref_path in enumerate(request.references[:4]):
            try:
                with Image.open(ref_path) as ref:
                    ref_img = ref.convert("RGB")
                scale = min(width, height) // (2 + index)
                ref_img.thumbnail((scale, scale))
                if index % 2 == 0:
                    ref_img = ref_img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                position = (rng.randint(0, max(0, width - ref_img.width)), rng.randint(0, max(0, height - ref_img.height)))
                overlay = Image.new("RGBA", ref_img.size, (0, 0, 0, 0))
                overlay.paste(ref_img, (0, 0))
                blended = Image.new("RGBA", img.size, (0, 0, 0, 0))
                blended.paste(overlay, position)
                img = Image.blend(img, Image.alpha_composite(img.convert("RGBA"), blended).convert("RGB"), 0.45)
                draw = ImageDraw.Draw(img, "RGBA")
            except OSError:
                continue

        self._draw_labels(draw, img, request, hue)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue()

    def _draw_labels(self, draw: ImageDraw.ImageDraw, img: Image.Image, request: GenerateRequest, hue: int) -> None:
        width, height = img.size
        font_size = max(14, min(width, height) // 28)
        small_size = max(11, min(width, height) // 44)

        def font(size: int):
            try:
                return ImageFont.load_default(size=size)
            except TypeError:  # Pillow antigo
                return ImageFont.load_default()

        big, small = font(font_size), font(small_size)

        # faixa inferior: aviso honesto
        band_h = max(40, height // 12)
        draw.rectangle((0, height - band_h, width, height), fill=(6, 8, 12, 210))
        draw.line([(0, height - band_h), (width, height - band_h)], fill=_hsl(hue, 0.8, 0.55, ), width=2)
        draw.text((width * 0.02, height - band_h + band_h * 0.14), "SIMULACAO - SEM IA", font=big, fill=(235, 240, 245))

        # recorte do prompt (ate 3 linhas)
        prompt = request.prompt.strip().replace("\n", " ")[:_MAX_LABEL_CHARS]
        if prompt:
            words, lines, line = prompt.split(), [], ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if len(candidate) > max(24, width // 10) and line:
                    lines.append(line)
                    line = word
                    if len(lines) == 3:
                        break
                else:
                    line = candidate
            if len(lines) < 3 and line:
                lines.append(line)
            y = height * 0.06
            for text in lines:
                draw.text((width * 0.02, y), text, font=big, fill=(235, 240, 245, 235))
                y += font_size * 1.35

        draw.text(
            (width * 0.02, height - band_h - small_size * 1.6),
            f"{width}x{height} | seed {request.seed} | refs {len(request.references)}",
            font=small,
            fill=(160, 172, 184),
        )
