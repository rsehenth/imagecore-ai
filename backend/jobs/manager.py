"""Fila de geracao com concorrencia 1: enfileira, executa, reporta progresso e cancela.

Um job por vez (asyncio.Queue + worker unico). Cancelamento cooperativo via asyncio.Event,
checado pelo provider em pontos seguros. Pre-flight de VRAM antes de geracoes reais (Etapa 8):
separa custo de carga a frio do custo incremental com pesos residentes; a residencia e
evidenciada pelo PROPRIO worker (memoria do processo do ComfyUI identificado pela porta,
historico do proprio ComfyUI ou geracao anterior desta sessao) — nunca pelo uso global da
GPU, que mistura outros aplicativos. A memoria livre preferida e a reportada pelo proprio
motor (/system_stats). Quando a residencia nao pode ser confirmada, a estimativa e
conservadora a frio. Nenhum processo externo (incluindo ComfyUI preexistente) e
encerrado por aqui e nenhum modelo compartilhado e descarregado automaticamente (o POST /free
existe apenas como acao controlada do usuario: instancia declarada DEDICADA ao ImageCore,
confirmacao e fila vazia comprovada).
"""

from __future__ import annotations

import asyncio
import logging

from backend.core.config import GpuSettings
from backend.core.gpu import gpu_evidence, gpu_snapshot
from backend.generation.base import (
    CancelCheck,
    GenerateRequest,
    GenerationCancelled,
    GenerationError,
    GenerationUnavailableError,
    ImageProvider,
    ProgressFn,
    VramInsufficientError,
)
from backend.storage.gallery import ConflictError, GenerationRepository
from backend.storage.uploads import ReferenceStore

logger = logging.getLogger(__name__)

_MAX_QUEUE = 4
_PROGRESS_WRITE_STEP = 3


class GenerationJobManager:
    def __init__(
        self,
        repo: GenerationRepository,
        provider: ImageProvider,
        uploads: ReferenceStore,
        *,
        gpu_cfg: GpuSettings,
    ):
        self.repo = repo
        self.provider = provider
        self.uploads = uploads
        self.gpu_cfg = gpu_cfg
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._worker_task: asyncio.Task | None = None
        self._current: str | None = None

    # ------------------------------------------------------------------ ciclo de vida

    async def start(self) -> None:
        # Recuperacao de boot: nada sobrevive a um reinicio do servidor.
        for row in self.repo.list(status="running"):
            self.repo.finish(row["id"], "failed", error="Servidor reiniciado durante a geracao.")
        for row in self.repo.list(status="queued"):
            event = asyncio.Event()
            self._cancel_events[row["id"]] = event
            try:
                self._queue.put_nowait(row["id"])
            except asyncio.QueueFull:  # fila some no reboot; registra a causa real
                self.repo.finish(row["id"], "failed", error="Fila indisponivel no reinicio do servidor.")
        self._worker_task = asyncio.create_task(self._worker(), name="imagecore-job-worker")

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - encerramento best effort
                pass
            self._worker_task = None

    # ------------------------------------------------------------------ API

    def submit(self, gen_id: str) -> None:
        if self._worker_task is None:
            raise ConflictError("Gerenciador de geracao nao esta em execucao.", code="manager_not_running")
        try:
            self._queue.put_nowait(gen_id)
        except asyncio.QueueFull as exc:
            raise ConflictError("Fila cheia: aguarde a geracao atual terminar.", code="queue_full") from exc
        self._cancel_events[gen_id] = asyncio.Event()

    def cancel(self, gen_id: str) -> bool:
        event = self._cancel_events.get(gen_id)
        if event is None:
            return False
        event.set()
        if self._current != gen_id:
            # Ainda na fila: finaliza ja; o worker vai pular ao chegar nele.
            self.repo.finish(gen_id, "cancelled")
        return True

    @property
    def current(self) -> str | None:
        return self._current

    # ------------------------------------------------------------------ worker

    async def _worker(self) -> None:
        while True:
            gen_id = await self._queue.get()
            event = self._cancel_events.get(gen_id) or asyncio.Event()
            try:
                if event.is_set():
                    self.repo.finish(gen_id, "cancelled")
                    continue
                self._current = gen_id
                await self._run(gen_id, event)
            except GenerationCancelled:
                self.repo.finish(gen_id, "cancelled")
            except (GenerationUnavailableError, VramInsufficientError) as exc:
                self.repo.finish(gen_id, "failed", error=str(exc))
            except GenerationError as exc:
                self.repo.finish(gen_id, "failed", error=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # nunca deixa o job preso em "running"
                logger.exception("Falha inesperada na geracao %s", gen_id)
                self.repo.finish(gen_id, "failed", error=f"Erro inesperado ({exc.__class__.__name__}).")
            finally:
                self._current = None
                self._cancel_events.pop(gen_id, None)
                self._queue.task_done()

    async def _run(self, gen_id: str, cancel_event: asyncio.Event) -> None:
        generation = self.repo.get(gen_id)
        if generation is None:
            return
        self.repo.set_running(gen_id)
        cancel: CancelCheck = cancel_event.is_set

        last_stage, last_pct = "", 0

        def progress(stage: str, pct: int) -> None:
            nonlocal last_stage, last_pct
            if stage == last_stage and abs(pct - last_pct) < _PROGRESS_WRITE_STEP and pct not in (0, 100):
                return
            last_stage, last_pct = stage, pct
            self.repo.set_progress(gen_id, stage, pct)

        await self._preflight(progress, width=int(generation["width"]), height=int(generation["height"]), mode=generation["params"].get("mode", "t2i") if generation["params"] else "t2i")

        reference_rows = self.uploads.get_many(generation["reference_ids"])
        references = [self.uploads.path_of(row) for row in reference_rows]
        params = generation["params"] or {}
        request = GenerateRequest(
            job_id=gen_id,
            prompt=generation["prompt"],
            width=int(generation["width"]),
            height=int(generation["height"]),
            seed=int(generation["seed"]),
            style=generation["style"],
            references=references,
            mode=params.get("mode", "t2i"),
            denoise=params.get("denoise"),
            steps=params.get("steps"),
            cfg=params.get("cfg"),
            sampler=params.get("sampler"),
            negative_prompt=params.get("negative_prompt"),
            profile=str(params.get("profile") or ""),
            expected_model=str(params.get("model") or ""),
        )
        result = await self.provider.generate(request, progress=progress, cancel=cancel)
        self.repo.add_image(
            generation_id=gen_id,
            kind="generated",
            mime=result.mime,
            ext=result.ext,
            data=result.data,
            width=result.width,
            height=result.height,
        )
        self.repo.finish(gen_id, "succeeded")
        logger.info("Geracao concluida | job=%s provider=%s %dx%d refs=%d", gen_id, self.provider.name, result.width, result.height, len(references))

    async def _preflight(self, progress: ProgressFn, *, width: int, height: int, mode: str) -> None:
        progress("verificando motor", 2)
        await self.provider.health()  # GenerationUnavailableError propaga com a mensagem certa
        estimate = getattr(self.provider, "estimate_vram_mb", None)
        if not self.gpu_cfg.enabled or not callable(estimate):
            return
        # Evidencia do PROPRIO worker (Etapa 8): memoria de GPU do processo do motor
        # (identificado pela porta) e residencia de pesos via warm_evidence — sessao,
        # processo e/ou historico do proprio ComfyUI (sobrevive a reinicios do backend,
        # o caso 'backend reiniciado com o ComfyUI ainda ativo'). Uso global da GPU e
        # diagnostico, nunca evidencia.
        base_url = getattr(getattr(self.provider, "cfg", None), "base_url", None)
        evidence = await asyncio.to_thread(gpu_evidence, base_url)
        comfyui_vram = evidence.get("comfyui_vram_mb")
        warm_fn = getattr(self.provider, "warm_evidence", None)
        warm_info = await warm_fn(comfyui_vram_mb=comfyui_vram) if callable(warm_fn) else None
        warm = (warm_info or {}).get("warm")
        estimate_kwargs = {"comfyui_vram_mb": comfyui_vram}
        if callable(warm_fn):
            estimate_kwargs["warm"] = warm
        required = estimate(width, height, mode, **estimate_kwargs)
        if required <= 0:
            return
        basis = None
        if warm is not None and isinstance(warm_info, dict):
            basis = warm_info.get("basis")
        if not basis:
            basis = (
                getattr(self.provider, "vram_basis", None)
                and self.provider.vram_basis(comfyui_vram)
            ) or "estimativa (nao medicao)"
        # Memoria livre: relatorio do PROPRIO motor (/system_stats) quando disponivel — e a
        # visao do worker que executara a geracao; fallback: snapshot do driver (nvidia-smi).
        free_fn = getattr(self.provider, "device_free_vram_mb", None)
        worker_free = await free_fn() if callable(free_fn) else None
        if worker_free is not None:
            free = worker_free
            details = f"{free} MB livres (relatorio do proprio motor)"
            snapshot = await asyncio.to_thread(gpu_snapshot)
            if snapshot:
                gpu = max(snapshot, key=lambda item: item.get("memory_total_mb") or 0)
                if gpu.get("memory_total_mb"):
                    details += f" de {gpu.get('memory_total_mb')} MB na {gpu.get('name', 'GPU')}"
        else:
            snapshot = await asyncio.to_thread(gpu_snapshot)
            if not snapshot:
                # sem nvidia-smi (ex.: iGPU) e sem relatorio do motor: confia no erro real
                return
            gpu = max(snapshot, key=lambda item: item.get("memory_total_mb") or 0)
            free = gpu.get("memory_free_mb")
            if free is None:
                return
            details = (
                f"{free} MB livres de {gpu.get('memory_total_mb', '?')} MB na {gpu.get('name', 'GPU')}"
                f" ({gpu.get('memory_used_mb', '?')} MB em uso no total)"
            )
        needed = required + self.gpu_cfg.min_free_vram_mb
        if free < needed:
            motor = (
                f"; processo do motor usando cerca de {comfyui_vram} MB"
                if comfyui_vram is not None
                else f"; {evidence.get('note') or 'memoria do motor nao identificada'}"
            )
            raise VramInsufficientError(
                f"VRAM livre insuficiente: {details}. Estimativa (NAO medicao) para {width}x{height} "
                f"no modo {mode}: {required} MB ({basis}) + folga {self.gpu_cfg.min_free_vram_mb} MB = "
                f"cerca de {needed} MB necessarios{motor}. Libere a memoria do proprio motor "
                "(Configuracoes > 'Liberar memoria do motor', executada apenas quando a "
                "instancia do ComfyUI e dedicada ao ImageCore AI e a fila esta comprovadamente "
                "vazia) e tente novamente.",
                code="vram_insufficient",
            )
        progress(f"vram ok ({basis}; estimativa, nao medicao)", 4)
