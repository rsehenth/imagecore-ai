"""Adaptador real para um ComfyUI local (submit/poll/cancel/download via API HTTP).

Dois workflows (API format) em config/workflows/ com mapeamentos proprios:
- texto->imagem (comfyui.workflow + comfyui.node_map + comfyui.ref_node_map);
- imagem->imagem (comfyui.workflow_i2i + comfyui.node_map_i2i + comfyui.ref_node_map_i2i),
  onde o latent vem da referencia via VAE (LoadImage -> VAEEncode) e a intensidade da
  transformacao e o denoise do KSampler.

Cancelamento (Etapa 8): cada geracao rastreia o prompt_id REAL retornado pelo ComfyUI
(o servidor 0.45+ so aceita prompt_id em formato UUID canonico; o ImageCore nunca fabrica
ids). O cancelamento prioriza o endpoint individual `POST /api/jobs/{prompt_id}/cancel`
(ComfyUI >= 0.45): idempotente, state-agnostic e ATOMICO (interrupt_if_running so sinaliza
se o prompt proprio ainda for o item em execucao) — pendentes sao removidos da fila e em
execucao sao interrompidos sem tocar em jobs de outras aplicacoes; o /interrupt GLOBAL nunca
e usado. Sem a API individual (ComfyUI anterior), comportamento conservador da Etapa 4:
pendente -> POST /queue {"delete": [prompt_id proprio]}; em execucao -> aguardar o fim do
prompt proprio. Em TODOS os caminhos, "cancelado" so e reportado com o estado terminal
EFETIVAMENTE determinado (API de jobs, execucao concluida/interrompida no historico, ou
fora da fila com a fila lida com exito e sem entrada no historico); fila/motor
indisponiveis sao INCONCLUSIVOS — o poll continua ate o terminal real ou o timeout, nunca
um cancelamento declarado sem prova (Etapa 8.1).

VRAM (Etapa 8.1): a estimativa e uma ESTIMATIVA (nunca medicao) que separa custo de carga a
frio do custo incremental com pesos residentes. A residencia e CLASSIFICADA em tres niveis —
"confirmada" (evidencia direta do worker de que os pesos ESTAO carregados: nenhuma API do
ComfyUI consultada aqui a expoe, portanto nunca e afirmada hoje), "provavel" (heuristica:
geracao bem-sucedida desta sessao do backend, memoria do PROCESSO do motor, ou historico do
proprio ComfyUI) e "desconhecida" (sem evidencia -> estimativa a frio). O uso global da GPU
de outros aplicativos nunca e evidencia. Erro CUDA OOM volta a estimativa a frio sem encerrar
processos (recuperacao controlada: o historico do ComfyUI mostra o erro e desmente qualquer
sinal quente anterior). O POST /free existe apenas como acao CONTROLADA (declaracao de
instancia dedicada + confirmacao + fila vazia verificada), nunca automatica.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from backend.core.config import ComfyUISettings
from backend.core.gpu import gpu_evidence
from backend.core.presets import QUALITY_PRESETS
from backend.generation.base import (
    CancelCheck,
    GenerateRequest,
    GenerateResult,
    GenerationCancelled,
    GenerationError,
    GenerationUnavailableError,
    ImageProvider,
    ProgressFn,
)
from backend.generation.profiles import PROFILES

logger = logging.getLogger(__name__)

_SDXL_PIXEL_BUDGET = 1024 * 1024
_I2I_EXTRA_VRAM_MB = 256  # estimativa: VAEEncode do latent da referencia (1 forward, sem gradientes)
# Pesos provavelmente ja residentes no ComfyUI (aparecem como "usados" no nvidia-smi): a nova
# geracao custa apenas o incremental (ativacoes/latents/VAE), nao os pesos de novo.
_WARM_INCREMENTAL = 0.2
# Evidencia do proprio worker: o PROCESSO do ComfyUI com pelo menos ~50% da estimativa base
# de VRAM indica pesos provavelmente residentes. O uso GLOBAL da GPU (outros apps, desktop)
# nunca e tratado como evidencia — memoria de terceiros nao prova nada sobre o nosso motor.
_WARM_PROCESS_RATIO = 0.5
# Historico do proprio ComfyUI consultado na deteccao de residencia (sobrevive a reinicios
# do backend; get_history?max_items devolve as ultimas execucoes em ordem de conclusao).
_HISTORY_MAX_ITEMS = 64
_MAX_ESTIMATE_FACTOR = 2.0
# Marcadores de falta de VRAM reportados pelo PyTorch/ComfyUI (recuperacao sem tocar em nada).
_OOM_MARKER = "out of memory"


def _set_path(document: dict, path: list[str], value: Any) -> None:
    """Escreve `value` em um caminho JSON tipo ["6", "inputs", "text"]."""
    cursor: Any = document
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            raise GenerationError(f"mapeamento invalido: caminho {' -> '.join(path)} nao existe no workflow")
        cursor = cursor[key]
    if not isinstance(cursor, dict):
        raise GenerationError(f"mapeamento invalido: caminho {' -> '.join(path)} nao existe no workflow")
    cursor[path[-1]] = value


class ComfyUIProvider(ImageProvider):
    name = "comfyui"

    def __init__(self, cfg: ComfyUISettings):
        self.cfg = cfg
        self.required_vram_mb = cfg.estimated_vram_mb
        self._client = httpx.AsyncClient(timeout=cfg.timeout_seconds, trust_env=False)
        self._prompt_ids: dict[str, str] = {}  # job_id -> prompt_id REAL devolvido pelo ComfyUI
        self._model_warm = False  # uma geracao bem-sucedida nesta sessao provavelmente carregou os pesos
        # Endpoint individual de cancelamento (POST /api/jobs/{prompt_id}/cancel, ComfyUI >= 0.45):
        # None = detectar na primeira tentativa; False = rota ausente (fallback conservador).
        self._job_cancel_api: bool | None = None

    # ------------------------------------------------------------------ saude

    async def health(self) -> dict:
        try:
            response = await self._client.get(f"{self.cfg.base_url}/system_stats", timeout=5.0)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GenerationUnavailableError(
                f"ComfyUI nao respondeu em {self.cfg.base_url} (timeout)."
            ) from exc
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(
                f"ComfyUI inacessivel em {self.cfg.base_url}: {exc.__class__.__name__}. "
                "Inicie o ComfyUI local e confira a porta em config/settings.yaml."
            ) from exc
        data = response.json() if response.status_code == 200 else {}
        return {"ok": True, "provider": self.name, "system": data.get("system", {}), "devices": data.get("devices", [])}

    # Nos que carregam os pesos principais -> campo do nome do modelo no workflow.
    _MODEL_LOADERS = {
        "CheckpointLoaderSimple": "ckpt_name",
        "CheckpointLoader": "ckpt_name",
        "UNETLoader": "unet_name",
        "UnetLoaderGGUF": "unet_name",
    }

    def capabilities(self) -> dict:
        i2i_slots = sorted(self.cfg.ref_node_map_i2i.keys())
        i2i_ok = self.cfg.workflow_i2i is not None and bool(i2i_slots)
        t2i_slots = sorted(self.cfg.ref_node_map.keys())
        model = self._workflow_model()
        note = (
            "Execucao real via ComfyUI. Capacidades dependem dos workflows configurados em config/workflows/."
            if i2i_ok or t2i_slots
            else "Nenhum workflow com LoadImage mapeado: referencias/img2img nao suportados (comfyui.ref_node_map*)."
        )
        return {
            "references": bool(t2i_slots or i2i_ok),
            "img2img": i2i_ok,
            "max_references": max([1 if i2i_ok else 0, *t2i_slots]) if (t2i_slots or i2i_ok) else 0,
            "native_profiles": "xl" in model.lower(),
            "quality_presets": sorted(QUALITY_PRESETS.keys()),
            "editable": bool(t2i_slots),  # um workflow texto->imagem com LoadImage aceita edicao
            "workflow": self.cfg.workflow.name,
            "workflow_i2i": self.cfg.workflow_i2i.name if self.cfg.workflow_i2i else None,
            "model": model,
            "negative_prompt_supported": "negative_prompt" in self.cfg.node_map,
            # Cancelamento individual (ComfyUI >= 0.45); None = detectado na 1a tentativa.
            "job_cancel_api": self._job_cancel_api,
            "note": note,
        }

    def _workflow_model(self) -> str:
        """Nome do checkpoint/unet declarado no workflow texto->imagem (identificacao honesta)."""
        for model in (self._model_of(self.cfg.workflow), self._model_of(self.cfg.workflow_i2i)):
            if model:
                return model
        return ""

    def _active_workflow_is(self, profile) -> bool:
        """O workflow texto->imagem ativo e exatamente o do perfil (uso do node_map do perfil)."""
        try:
            return Path(self.cfg.workflow).resolve() == Path(profile.workflow_t2i).resolve()
        except (OSError, ValueError):
            return False

    def _model_of(self, workflow_path: Path | None) -> str:
        if workflow_path is None:
            return ""
        try:
            workflow = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(workflow, dict):
            return ""
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            key = self._MODEL_LOADERS.get(str(node.get("class_type", "")))
            if key:
                return str((node.get("inputs") or {}).get(key, "") or "")
        return ""

    # ------------------------------------------------------------------ VRAM (estimativa)

    def estimate_vram_mb(
        self, width: int, height: int, mode: str = "t2i", comfyui_vram_mb: int | None = None,
        warm: bool | None = None,
    ) -> int:
        """Estimativa (NAO medicao) por resolucao, modo e evidencia do proprio worker.

        A frio (pesos nao carregados): custo completo = pesos + ativacoes. Com residencia
        evidenciada — geracao anterior bem-sucedida DESTE backend, o processo do ComfyUI
        identificado com memoria compativel com os pesos, ou o historico do proprio ComfyUI
        (Etapa 8; sobrevive a reinicios do backend) — cobra apenas o incremental, pois os
        pesos ja figuram como "usados" na memoria livre reportada pelo driver.
        `warm=True/False` e evidencia explicita (warm_evidence); None aplica a inferencia
        legada (sessao + processo do motor). `comfyui_vram_mb` vem do PROCESSO do motor
        (gpu_evidence); uso global da GPU de outros aplicativos nunca e usado como evidencia
        de residencia."""
        pixels = max(1, int(width) * int(height))
        factor = min(_MAX_ESTIMATE_FACTOR, max(1.0, pixels / _SDXL_PIXEL_BUDGET))
        if warm is None:
            warm = self._model_warm or (
                comfyui_vram_mb is not None
                and comfyui_vram_mb >= self.cfg.estimated_vram_mb * _WARM_PROCESS_RATIO
            )
        if warm:
            estimate = self.cfg.estimated_vram_mb * _WARM_INCREMENTAL * factor
        else:
            estimate = self.cfg.estimated_vram_mb * factor
        if mode == "i2i":
            estimate += _I2I_EXTRA_VRAM_MB
        return int(estimate)

    def vram_basis(self, comfyui_vram_mb: int | None, warm: bool | None = None) -> str:
        """Base da estimativa, para mensagens honestas: 'sessao', 'processo do motor' ou 'conservador'."""
        if warm is True:
            return "pesos provavelmente residentes (evidencia do proprio worker)"
        if warm is False:
            return "estimativa a frio (residencia dos pesos nao confirmada pelo worker)"
        if self._model_warm:
            return "pesos provavelmente residentes (geracao anterior desta sessao)"
        if comfyui_vram_mb is not None and comfyui_vram_mb >= self.cfg.estimated_vram_mb * _WARM_PROCESS_RATIO:
            return "pesos provavelmente residentes (memoria do processo do motor)"
        if comfyui_vram_mb is None:
            return "estimativa conservadora a frio (residencia dos pesos nao confirmada)"
        return "estimativa a frio"

    # ------------------------------------------------------- residencia e memoria (Etapa 8)

    async def warm_evidence(self, comfyui_vram_mb: int | None = None) -> dict:
        """Evidencia de residencia de pesos, CLASSIFICADA (Etapa 8.1) em tres niveis:

        - "confirmada": evidencia DIRETA do worker de que os pesos estao carregados agora.
          Nenhuma API do ComfyUI consultada aqui (system_stats/fila/historico) expoe modelos
          carregados, portanto este nivel NUNCA e afirmado hoje — o campo existe para a
          taxonomia ficar explicita em vez de disfarcar heuristica de certeza.
        - "provavel": heuristica — geracao bem-sucedida desta sessao do backend, memoria de
          VRAM do PROCESSO do motor (quando o driver informa — WDDM costuma omitir) ou
          historico do proprio ComfyUI (sobrevive a reinicios do backend).
        - "desconhecida": sem evidencia — estimativa a frio.

        O historico e HEURISTICA com recuperacao controlada apos CUDA OOM: a ultima execucao
        com carga de modelo terminando em erro (o ComfyUI descarrega TODOS os modelos) ou com
        OUTRO modelo desmente qualquer sinal quente anterior (sessao incluida — o erro pode
        ter vindo de outro cliente e descarregado tudo). O uso global da GPU de outros
        aplicativos NUNCA e evidencia. Somente leitura: nunca descarrega modelos nem altera
        o estado do motor."""
        history = await self._history_model_evidence()
        if self._model_warm:
            if history.get("warm") is False:
                # Recuperacao controlada: o historico (mais recente que a geracao da sessao)
                # desmente a sessao — OOM ou troca de modelo descarregaram os pesos.
                self._model_warm = False
                logger.warning(
                    "Historico do ComfyUI desmente a residencia da sessao (erro/troca de modelo "
                    "mais recente): estimativa volta a frio."
                )
            else:
                return {
                    "warm": True,
                    "residency": "provavel",
                    "basis": "pesos provavelmente residentes (geracao anterior desta sessao do backend)",
                }
        if history.get("warm") is False:
            return {
                "warm": False,
                "residency": "desconhecida",
                "basis": (
                    "estimativa a frio (residencia dos pesos nao confirmada pelo worker; "
                    "historico mais recente indica erro ou outro modelo)"
                ),
                "note": history.get("note", ""),
            }
        if comfyui_vram_mb is not None:
            if comfyui_vram_mb >= self.cfg.estimated_vram_mb * _WARM_PROCESS_RATIO:
                return {
                    "warm": True,
                    "residency": "provavel",
                    "basis": (
                        "pesos provavelmente residentes (processo do motor usando "
                        f"~{comfyui_vram_mb} MB de VRAM)"
                    ),
                }
            return {
                "warm": False,
                "residency": "desconhecida",
                "basis": (
                    f"processo do motor com apenas ~{comfyui_vram_mb} MB: residencia dos pesos "
                    "nao confirmada"
                ),
            }
        if history.get("warm") is True:
            return {
                "warm": True,
                "residency": "provavel",
                "basis": "pesos provavelmente residentes (heuristica do historico do proprio ComfyUI)",
                "note": history.get("note", ""),
            }
        return {
            "warm": None,
            "residency": "desconhecida",
            "basis": "estimativa conservadora a frio (residencia dos pesos nao confirmada)",
            "note": history.get("note", ""),
        }

    async def _history_model_evidence(self) -> dict:
        """HEURISTICA de residencia pelo HISTORICO do proprio ComfyUI (nunca pelo uso global
        da GPU). Historico nao prova que os pesos AINDA estao carregados — so registra o que
        aconteceu na ultima execucao com carga de modelo; a leitura e sempre 'provavel'/
        'nao confirmada', nunca 'confirmada'.

        A ultima execucao com carga de modelo decide (get_history preserva a ordem de
        conclusao): sucesso com o modelo esperado -> pesos provavelmente residentes;
        interrupcao dele -> idem (o ComfyUI 0.45 nao descarrega modelos ao interromper); erro
        -> residencia nao confirmada (o OOM descarrega TODOS os modelos no ComfyUI —
        recuperacao controlada: a estimativa volta a frio); sucesso ou interrupcao com OUTRO
        modelo -> residencia nao confirmada (a ultima carga foi de outro arquivo: pode ter
        sido trocado)."""
        unknown = {"warm": None, "note": "historico do ComfyUI indisponivel: residencia dos pesos nao confirmada."}
        expected = self._workflow_model()
        if not expected:
            return {"warm": None, "note": "workflow sem modelo declarado: residencia nao confirmada."}
        try:
            response = await self._client.get(
                f"{self.cfg.base_url}/history", params={"max_items": _HISTORY_MAX_ITEMS}, timeout=10.0
            )
        except httpx.TransportError:
            return unknown
        if response.status_code != 200:
            return unknown
        try:
            history = response.json()
        except (json.JSONDecodeError, ValueError):
            return unknown
        if not isinstance(history, dict):
            return unknown
        decision: dict | None = None
        for entry in history.values():
            if not isinstance(entry, dict):
                continue
            prompt = entry.get("prompt")
            workflow = (
                prompt[2]
                if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], dict)
                else {}
            )
            uses_expected = False
            others: set[str] = set()
            for node in workflow.values():
                if not isinstance(node, dict):
                    continue
                for value in (node.get("inputs") or {}).values():
                    if isinstance(value, str) and value.lower().endswith(".safetensors"):
                        if value == expected:
                            uses_expected = True
                        else:
                            others.add(value)
            if not (uses_expected or others):
                continue  # execucao sem carga de modelo nao comprova residencia
            status = entry.get("status") or {}
            messages = {m[0] for m in (status.get("messages") or []) if isinstance(m, list) and m}
            other = ", ".join(sorted(others))
            if status.get("status_str") == "success":
                if uses_expected:
                    decision = {
                        "warm": True,
                        "note": f"historico do ComfyUI: ultima execucao com carga de modelo usou '{expected}' com sucesso.",
                    }
                else:
                    decision = {
                        "warm": False,
                        "note": (
                            "historico do ComfyUI: ultima execucao com carga de modelo usou outro modelo "
                            f"('{other}'): residencia de '{expected}' nao confirmada (pode ter sido trocado)."
                        ),
                    }
            elif "execution_interrupted" in messages:
                if uses_expected:
                    decision = {
                        "warm": True,
                        "note": (
                            f"historico do ComfyUI: ultima execucao com carga de modelo usou '{expected}' e foi "
                            "interrompida (o ComfyUI mantem os pesos carregados ao interromper)."
                        ),
                    }
                else:
                    decision = {
                        "warm": False,
                        "note": (
                            "historico do ComfyUI: ultima execucao com carga de modelo usou outro modelo "
                            f"('{other}') e foi interrompida: residencia de '{expected}' nao confirmada."
                        ),
                    }
            elif "execution_error" in messages:
                decision = {
                    "warm": False,
                    "note": (
                        "historico do ComfyUI: ultima execucao com carga de modelo terminou em erro "
                        "(o ComfyUI descarrega os modelos apos OOM): residencia nao confirmada."
                    ),
                }
            else:
                decision = None  # terminal incerto: continua a varredura (entradas mais antigas)
        return decision or {
            "warm": None,
            "note": "historico sem execucoes com carga de modelo: residencia nao confirmada.",
        }

    async def device_free_vram_mb(self) -> int | None:
        """VRAM livre reportada pelo PROPRIO ComfyUI (/system_stats -> torch.cuda.mem_get_info).

        Fonte preferida do pre-flight: e a visao do worker que executara a geracao (e evita
        depender do nvidia-smi). None = indisponivel (o chamador usa o snapshot do driver)."""
        try:
            response = await self._client.get(f"{self.cfg.base_url}/system_stats", timeout=5.0)
        except httpx.TransportError:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            return None
        devices = data.get("devices") or []
        frees = [
            device.get("vram_free")
            for device in devices
            if isinstance(device, dict) and isinstance(device.get("vram_free"), int)
        ]
        return max(frees) if frees else None

    async def controlled_free(self, *, confirm: bool = False) -> dict:
        """POST /free de maneira CONTROLADA (Etapa 8.1): exige (1) a declaracao de que esta
        instancia do ComfyUI e DEDICADA ao ImageCore AI (comfyui.dedicated) — fila vazia nao
        prova exclusividade e, sem dedicacao comprovada pelo operador, a acao fica
        INDISPONIVEL (descarregaria pesos de outros clientes); (2) confirmacao explicita;
        (3) fila do ComfyUI VAZIA verificada com exito (fila ilegivel = recusa, nunca 'vazia'
        por omissao). Descarrega os modelos residentes e libera o cache do alocador. Nunca e
        chamado automaticamente pelo ImageCore e nunca encerra processos."""
        if not confirm:
            raise GenerationError(
                "Liberacao de memoria exige confirmacao explicita (confirm=true): descarrega os "
                "modelos residentes no ComfyUI e a proxima geracao recarrega os pesos.",
                code="free_missing_confirm",
            )
        if not self.cfg.dedicated:
            raise GenerationError(
                "Liberacao de memoria indisponivel: ela so e permitida em uma instancia do "
                "ComfyUI dedicada ao ImageCore AI (declare comfyui.dedicated: true em "
                "config/settings.yaml apenas se NENHUM outro cliente usar esta instancia). "
                "Fila vazia nao prova exclusividade — em uma instancia compartilhada o "
                "descarregamento afetaria pesos de outros clientes.",
                code="free_not_dedicated",
            )
        try:
            queue_response = await self._client.get(f"{self.cfg.base_url}/queue", timeout=5.0)
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(
                f"ComfyUI inacessivel ao verificar a fila: {exc.__class__.__name__}."
            ) from exc
        if queue_response.status_code != 200:
            # Fail closed: fila nao verificada nao e fila vazia.
            raise GenerationError(
                f"Fila do ComfyUI nao pôde ser verificada (HTTP {queue_response.status_code}): "
                "liberacao recusada — so e segura com a fila comprovadamente vazia.",
                code="free_queue_unverifiable",
            )
        try:
            queue = queue_response.json()
            busy = len(queue.get("queue_running") or []) + len(queue.get("queue_pending") or [])
        except (json.JSONDecodeError, ValueError, AttributeError):
            raise GenerationError(
                "Resposta ilegivel da fila do ComfyUI: liberacao recusada (a fila precisa estar "
                "comprovadamente vazia, inclusive de trabalhos de outras aplicacoes).",
                code="free_queue_unverifiable",
            ) from None
        if busy:
            raise GenerationError(
                f"Fila do ComfyUI nao esta vazia ({busy} trabalho(s) em execucao ou pendentes, "
                "inclusive de outras aplicacoes): liberar memoria agora afetaria trabalhos "
                "externos. Aguarde a fila esvaziar.",
                code="free_queue_not_empty",
            )
        try:
            response = await self._client.post(
                f"{self.cfg.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=30.0,
            )
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(
                f"ComfyUI inacessivel ao liberar memoria: {exc.__class__.__name__}."
            ) from exc
        if response.status_code != 200:
            raise GenerationError(f"ComfyUI recusou a liberacao de memoria (HTTP {response.status_code}).", code="free_refused")
        self._model_warm = False  # pesos foram descarregados: a proxima estimativa volta a frio
        logger.info("Memoria do ComfyUI liberada de maneira controlada (POST /free; instancia dedicada, fila vazia).")
        return {
            "freed": True,
            "note": "Modelos descarregados no ComfyUI (POST /free controlado; instancia dedicada, fila vazia). A proxima geracao recarrega os pesos (mais lenta).",
        }

    # ------------------------------------------------------------------ geracao

    async def generate(
        self, request: GenerateRequest, *, progress: ProgressFn, cancel: CancelCheck
    ) -> GenerateResult:
        workflow, node_map, ref_map = self._workflow_pack(request.mode)
        # Etapa 8 (perfis): quando o workflow ativo e EXATAMENTE o do perfil pedido, o
        # mapeamento EFETIVO de nos e o do proprio perfil (contrato do modelo — ex.: Z-Image
        # nao tem negative prompt; o mapa global do settings.yaml e do SDXL). Fallback: mapa
        # global para workflows sem perfil registrado.
        profile = PROFILES.get(request.profile or "") if request.profile else None
        if profile is not None and self._active_workflow_is(profile):
            if request.mode == "i2i" and profile.node_map_i2i:
                node_map = profile.node_map_i2i
                ref_map = profile.ref_node_map_i2i or ref_map
            elif request.mode == "t2i":
                node_map = profile.node_map
        # Etapa 7 (perfis): defesa contra substituicao silenciosa. O workflow carregado
        # DEVE apontar para o modelo do perfil pedido; caso contrario, falha ANTES de
        # enviar qualquer coisa ao ComfyUI (a fila pode ter esperado uma troca de config).
        if request.expected_model:
            detected = self._workflow_model()
            if detected and detected != request.expected_model:
                raise GenerationError(
                    f"Perfil '{request.profile}' exige o modelo '{request.expected_model}', mas o "
                    f"workflow configurado aponta para '{detected}'. Recusa de substituicao silenciosa.",
                    code="model_mismatch",
                )
        progress("preparando workflow", 3)

        if request.references:
            if len(request.references) != 1 or not ref_map:
                raise GenerationError(
                    "O modo imagem para imagem requer exatamente 1 referencia mapeada no workflow (ref_node_map_i2i)."
                )
            target = ref_map[1]
            prepared = await self._prepare_i2i_reference(request, progress)
            uploaded_name = await self._upload_reference_bytes(prepared)
            _set_path(workflow, target, uploaded_name)

        for field, value in (
            ("prompt", request.prompt),
            ("negative_prompt", self._negative_for(request, node_map)),
            ("width", int(request.width)),
            ("height", int(request.height)),
            ("seed", int(request.seed)),
            ("denoise", request.denoise),
            ("steps", request.steps),
            ("cfg", request.cfg),
            ("sampler", request.sampler),
        ):
            if value is None:
                continue
            path = node_map.get(field)
            if path:
                _set_path(workflow, path, value)

        progress("enviando ao ComfyUI", 6)
        prompt_id = await self._submit(workflow, request.job_id)
        self._prompt_ids[request.job_id] = prompt_id
        try:
            progress("na fila do ComfyUI", 10)
            outputs = await self._wait_outputs(prompt_id, request.job_id, progress, cancel)
        finally:
            self._prompt_ids.pop(request.job_id, None)
        progress("baixando resultado", 92)
        data, meta = await self._download(outputs[0])
        self._model_warm = True
        mode_label = "img2img" if request.mode == "i2i" else "t2i"
        return GenerateResult(
            data=data,
            mime="image/png",
            ext="png",
            width=int(meta.get("width") or request.width),
            height=int(meta.get("height") or request.height),
            note=f"Gerado pelo ComfyUI ({mode_label}, {self.cfg.workflow.name if request.mode != 'i2i' else (self.cfg.workflow_i2i.name if self.cfg.workflow_i2i else '')}).",
        )

    def _negative_for(self, request: GenerateRequest, node_map: dict[str, list[str]] | None = None) -> str | None:
        """Texto do no CLIP negativo. None = modo sem mapeamento (nao toca no no); string =
        o que sera enviado: pedido do usuario ou, sem pedido, o padrao do config."""
        mapping = node_map if node_map is not None else (
            self.cfg.node_map_i2i if request.mode == "i2i" else self.cfg.node_map
        )
        if "negative_prompt" not in mapping:
            return None
        if request.negative_prompt is None:
            return self.cfg.default_negative_prompt.strip()
        return request.negative_prompt.strip()

    async def cancel_current(self, job_id: str | None = None) -> str | None:
        """Cancelamento seguro do prompt proprio. Retorna a acao tomada:

        - "requested": POST /api/jobs/{prompt_id}/cancel despachado (ComfyUI >= 0.45) —
          pendente foi removido da fila; em execucao foi interrompido ATOMICAMENTE
          (interrupt_if_running so sinaliza se o prompt proprio ainda for o item em
          execucao — nunca cai no job seguinte). O chamador confirma o estado terminal.
        - "removed": fallback para job pendente sem a API individual ->
          POST /queue {"delete": [prompt_id proprio]} (cirurgico). A remocao NAO confirma
          o cancelamento por si so (corrida: o item pode ter passado a executar entre a
          leitura da fila e o delete) — o chamador confirma o terminal (fora da fila e sem
          entrada no historico) antes de reportar "cancelado".
        - "awaiting": em execucao sem a API individual -> nada e enviado (o /interrupt
          GLOBAL nunca e usado); o chamador aguarda o fim do prompt proprio antes de
          reportar "cancelado";
        - "inconclusive": estado NAO determinado (fila indisponivel/ilegivel apos falha
          da API individual) — o chamador NAO pode reportar cancelamento confirmado;
          continua o poll ate o terminal real (ou o timeout).
        - None: job sem prompt proprio submetido, prompt ja terminal confirmado
          (concluiu — a API individual responde {"cancelled": false}; fora da fila com a
          fila lida com exito) ou motor inacessivel de forma conclusiva.

        Somente o prompt_id REAL devolvido pelo ComfyUI e usado; jobs de outras aplicacoes
        nunca sao removidos nem interrompidos."""
        prompt_id = self._prompt_ids.get(job_id or "")
        if not prompt_id:
            return None
        dispatched = await self._cancel_job_via_api(prompt_id)
        if dispatched is True:
            logger.info(
                "Cancelamento individual despachado no ComfyUI (prompt_id=%s, job %s)", prompt_id, job_id
            )
            return "requested"
        if dispatched is False:
            # {"cancelled": false}: job terminal/desconhecido — sem execucao ativa do nosso prompt.
            return None
        # API individual indisponivel/inconclusiva (ComfyUI anterior, erro de transporte ou
        # 5xx): decide pelo estado na fila (leitura) sem tocar em nada.
        state = await self._queue_state(prompt_id)
        if state == "pending":
            try:
                await self._client.post(
                    f"{self.cfg.base_url}/queue", json={"delete": [prompt_id]}, timeout=5.0
                )
            except httpx.TransportError:
                return "inconclusive"  # delete nao confirmado: estado nao determinado
            logger.info("Job %s removido da fila do ComfyUI (prompt_id=%s)", job_id, prompt_id)
            return "removed"
        if state == "running":
            logger.info(
                "Job %s em execucao sem a API individual de cancelamento: aguardando o fim do "
                "prompt proprio (%s); /interrupt global nunca e usado",
                job_id,
                prompt_id,
            )
            return "awaiting"
        if state == "absent":
            # Fila lida com exito e o prompt nao esta nela: ja concluiu — terminal.
            return None
        # Fila indisponivel/ilegivel: estado terminal NAO determinado.
        return "inconclusive"

    async def _cancel_job_via_api(self, prompt_id: str) -> bool | None:
        """POST /api/jobs/{prompt_id}/cancel (ComfyUI >= 0.45, idempotente e state-agnostic).

        True  = cancelamento despachado (job proprio estava pendente ou em execucao);
        False = {"cancelled": false}: job terminal/desconhecido (no-op no servidor);
        None  = nao despachado por essa via (rota 404 em versao anterior — memorizada, erro
                de transporte ou HTTP inesperado): o estado NAO foi determinado por aqui e
                o fallback por leitura da fila decide. Usa SEMPRE o prompt_id real devolvido
                pelo ComfyUI (UUID)."""
        if self._job_cancel_api is False:
            return None
        try:
            response = await self._client.post(
                f"{self.cfg.base_url}/api/jobs/{prompt_id}/cancel", timeout=5.0
            )
        except httpx.TransportError:
            return None
        if response.status_code == 404:
            # Rota inexistente (ComfyUI anterior a 0.45): memoriza e usa o fallback conservador.
            self._job_cancel_api = False
            return None
        if response.status_code != 200:
            return None
        self._job_cancel_api = True
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return None
        return bool(payload.get("cancelled"))

    async def _job_status(self, prompt_id: str) -> dict | bool | None:
        """Estado oficial do job proprio na API /api/jobs (ComfyUI >= 0.45).

        dict  = {"status": "pending"|"in_progress"|"completed"|"failed"|"cancelled", ...};
        False = job ausente (HTTP 404: removido da fila antes de executar ou fora do historico)
                — estado terminal;
        None  = endpoint indisponivel/erro de transporte (inconclusivo: o chamador continua)."""
        try:
            response = await self._client.get(f"{self.cfg.base_url}/api/jobs/{prompt_id}", timeout=10.0)
        except httpx.TransportError:
            return None
        if response.status_code == 404:
            return False  # terminal: removido da fila antes de executar (ou fora do historico)
        if response.status_code != 200:
            return None  # 5xx etc.: inconclusivo — o poll seguinte decide
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ workflows

    def _workflow_pack(self, mode: str) -> tuple[dict, dict[str, list[str]], dict[int, list[str]]]:
        if mode == "i2i":
            if self.cfg.workflow_i2i is None or not self.cfg.ref_node_map_i2i:
                raise GenerationError(
                    "img2img nao configurado: defina comfyui.workflow_i2i e ref_node_map_i2i em config/settings.yaml.",
                    code="i2i_not_configured",
                )
            return self._load_workflow(self.cfg.workflow_i2i), self.cfg.node_map_i2i, self.cfg.ref_node_map_i2i
        return self._load_workflow(self.cfg.workflow), self.cfg.node_map, self.cfg.ref_node_map

    def _load_workflow(self, path: Path) -> dict:
        if not path.is_file():
            raise GenerationError(
                f"Workflow nao encontrado: {path}. Configure comfyui.workflow em config/settings.yaml.",
                code="workflow_missing",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise GenerationError(f"Workflow invalido ({path.name}): {exc.__class__.__name__}.", code="workflow_invalid") from exc
        if not isinstance(data, dict) or not data:
            raise GenerationError(f"Workflow invalido ({path.name}): esperado um objeto API format.", code="workflow_invalid")
        return data

    # ------------------------------------------------------------------ referencias (i2i)

    async def _prepare_i2i_reference(self, request: GenerateRequest, progress: ProgressFn) -> bytes:
        """Preparo seguro: reescala a referencia para as dimensoes seguras do latent (sandbox isolado)."""
        from backend.storage.image_sandbox import SandboxRejected, run_image_job

        ref_path = request.references[0]
        progress("preparando referencia", 5)
        try:
            data = ref_path.read_bytes()
        except OSError as exc:
            raise GenerationError(f"Falha ao ler referencia para envio: {exc.__class__.__name__}.") from exc
        try:
            _, resized = await asyncio.to_thread(
                run_image_job,
                "resize",
                data,
                {
                    "width": int(request.width),
                    "height": int(request.height),
                    "format": "png",
                    # Limites de ORIGEM (a referencia ja foi normalizada no upload com os mesmos
                    # tetos); o alvo e sempre <= ~1 MP, definido pelo orcamento do SDXL.
                    "max_side": 8192,
                    "max_pixels": 25_000_000,
                },
                timeout=60.0,
                memory_limit=1024 * 1024 * 1024,
                max_output=64 * 1024 * 1024,
            )
        except SandboxRejected as exc:
            raise GenerationError(f"Referencia rejeitada no preparo seguro: {exc}") from exc
        return resized

    async def _upload_reference_bytes(self, data: bytes) -> str:
        try:
            response = await self._client.post(
                f"{self.cfg.base_url}/upload/image",
                files={"image": ("imagecore-reference.png", data, "image/png")},
                data={"overwrite": "true", "type": "input"},
                timeout=30.0,
            )
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(f"ComfyUI inacessivel durante upload de referencia: {exc.__class__.__name__}.") from exc
        if response.status_code != 200:
            raise GenerationError(f"ComfyUI recusou a referencia (HTTP {response.status_code}).")
        name = response.json().get("name")
        if not name:
            raise GenerationError("ComfyUI nao retornou o nome do arquivo enviado.")
        return str(name)

    async def _upload_reference(self, path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise GenerationError(f"Falha ao ler referencia para envio: {exc.__class__.__name__}.") from exc
        return await self._upload_reference_bytes(data)

    # ------------------------------------------------------------------ ComfyUI API

    async def _submit(self, workflow: dict, client_id: str) -> str:
        """Submete o workflow e devolve o prompt_id REAL retornado pelo ComfyUI.

        O ComfyUI 0.45+ so aceita prompt_id em formato UUID canonico (ids fabricados sao
        rejeitados com 400), portanto o ImageCore NUNCA envia um id proprio: o servidor
        gera o UUID e o rastreamento (cancelamento incluido) usa exclusivamente esse id
        retornado. `client_id` carrega o job_id do ImageCore apenas para identificacao."""
        try:
            response = await self._client.post(
                f"{self.cfg.base_url}/prompt", json={"prompt": workflow, "client_id": client_id}
            )
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(f"ComfyUI inacessivel ao enfileirar: {exc.__class__.__name__}.") from exc
        if response.status_code != 200:
            detail = ""
            node_errors = ""
            try:
                payload = response.json()
                error = payload.get("error")
                detail = error.get("message", "") if isinstance(error, dict) else str(error or "")
                errors = payload.get("node_errors") or {}
                if errors:
                    node_errors = "; " + "; ".join(
                        f"no {node}: {', '.join(err.get('details', []) or [err.get('message', '')])}"
                        if isinstance(err, dict)
                        else str(err)
                        for node, err in list(errors.items())[:3]
                    )
            except (json.JSONDecodeError, ValueError):
                pass
            raise GenerationError(
                f"ComfyUI recusou o workflow (HTTP {response.status_code}){': ' + detail if detail else ''}{node_errors}. "
                "Verifique o modelo/workflow configurado."
            )
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise GenerationError("ComfyUI nao retornou prompt_id.")
        return str(prompt_id)

    async def _queue_state(self, prompt_id: str) -> str | None:
        """Posicao do prompt na fila do ComfyUI: 'running' | 'pending' | 'absent' | None.

        'absent' = a fila FOI lida com exito e o prompt NAO esta nela (terminal: ja concluiu
        ou foi removido). None = fila INDISPONIVEL (erro de transporte/HTTP) — o estado NAO
        foi determinado; o chamador NUNCA deve tratar None como 'ausente'."""
        try:
            response = await self._client.get(f"{self.cfg.base_url}/queue", timeout=10.0)
        except httpx.TransportError:
            return None  # indisponibilidade: estado nao determinado (diferente de 'absent')
        if response.status_code != 200:
            return None
        try:
            queue = response.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(queue, dict):
            return None
        for section, label in (("queue_running", "running"), ("queue_pending", "pending")):
            for entry in queue.get(section) or []:
                if isinstance(entry, (list, tuple)) and len(entry) > 1 and str(entry[1]) == prompt_id:
                    return label
        return "absent"

    async def _wait_outputs(self, prompt_id: str, job_id: str, progress: ProgressFn, cancel: CancelCheck) -> list[dict]:
        waited = 0.0
        seen_running = False
        awaiting_cancel = False  # cancelamento pedido: aguardar/confirmar o estado terminal do prompt proprio
        removed_awaiting = False  # "removed" da API legada: o delete nao confirma sozinho (corrida com execucao)
        history_seen = False  # entrada do prompt proprio ja apareceu no historico
        while waited < self.cfg.timeout_seconds:
            if cancel():
                if not awaiting_cancel:
                    action = await self.cancel_current(job_id)
                    if action in ("requested", "awaiting", "removed", "inconclusive"):
                        awaiting_cancel = True
                        removed_awaiting = action == "removed"
                        progress("cancelando: confirmando o estado terminal no ComfyUI", 15)
                    else:
                        # Sem execucao ativa do prompt proprio (nao submetido ou terminal
                        # confirmado pela API individual): o cancelamento e confirmado e
                        # qualquer resultado e descartado.
                        raise GenerationCancelled("Geracao cancelada pelo usuario.")
                else:
                    progress("cancelando: confirmando o estado terminal no ComfyUI", 15)
            await asyncio.sleep(self.cfg.poll_interval_seconds)
            waited += self.cfg.poll_interval_seconds
            if not seen_running:
                state = await self._queue_state(prompt_id)
                if state == "pending":
                    progress("aguardando fila do ComfyUI", 10)
                elif state == "running":
                    seen_running = True
                    progress("executando no ComfyUI", 15)
            if awaiting_cancel and self._job_cancel_api is True:
                # Confirmacao do estado terminal pela API de jobs do ComfyUI (0.45+): o
                # cancelamento so e apresentado como concluido apos o terminal real —
                # pendente removido antes de executar (404) ou interrompido ("cancelled").
                # "failed"/"completed" ficam para o fluxo de historico abaixo (erro real e
                # corrida com conclusao tem la o tratamento proprio).
                job = await self._job_status(prompt_id)
                if job is False or (isinstance(job, dict) and job.get("status") == "cancelled"):
                    raise GenerationCancelled(
                        "Cancelamento confirmado no ComfyUI: o prompt proprio atingiu estado terminal e o resultado foi descartado."
                    )
                # "pending"/"in_progress"/"completed"/"failed"/inconclusivo seguem no poll.
            if awaiting_cancel and removed_awaiting and not history_seen:
                # API legada ("removed"): o delete NAO confirma o cancelamento por si so —
                # na corrida o item pode ter passado a executar entre a leitura da fila e o
                # delete. Terminal so quando a fila (lida com exito) nao tem o prompt E o
                # historico ainda nao tem entrada dele (nunca executou). Se a entrada do
                # historico aparecer, o fluxo abaixo descarta o resultado como corrida.
                state = await self._queue_state(prompt_id)
                if state == "absent":
                    raise GenerationCancelled(
                        "Cancelamento confirmado no ComfyUI: o prompt proprio foi removido da "
                        "fila antes de executar (sem execucao e sem entrada no historico)."
                    )
                # "running"/"pending"/None (indisponivel): estado ainda nao determinado — segue o poll.
            if seen_running and int(waited) % 5 == 0 and not awaiting_cancel:
                progress("amostrando", min(90, 15 + int(waited)))
            try:
                response = await self._client.get(f"{self.cfg.base_url}/history/{prompt_id}", timeout=10.0)
            except httpx.TransportError:
                continue  # instante de indisponibilidade: o proximo poll decide
            if response.status_code != 200:
                continue
            history = response.json().get(prompt_id)
            if not history:
                continue
            history_seen = True
            status = history.get("status") or {}
            message_names = {message[0] for message in (status.get("messages") or []) if message}
            if "execution_interrupted" in message_names:
                raise GenerationCancelled("Geracao interrompida no ComfyUI (cancelamento solicitado).")
            if status.get("status_str") == "error" or status.get("completed") is False:
                raise GenerationError(self._extract_error(status))
            if awaiting_cancel:
                # O prompt proprio concluiu no ComfyUI na corrida com o cancelamento:
                # a execucao terminou — o cancelamento e reportado e o resultado (nao
                # desejado) e descartado.
                raise GenerationCancelled(
                    "Cancelamento concluido: o ComfyUI terminou o prompt proprio antes da interrupcao; resultado descartado."
                )
            outputs: dict = history.get("outputs") or {}
            images = [
                {"filename": item.get("filename"), "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")}
                for node_outputs in outputs.values()
                for item in (node_outputs.get("images") or [])
                if item.get("type") == "output" and item.get("filename")
            ]
            if images:
                return images
            if status.get("completed"):
                raise GenerationError("O ComfyUI concluiu a execucao sem produzir imagem de saida.")
        raise GenerationError("Tempo limite excedido aguardando o ComfyUI.")

    def _extract_error(self, status: dict) -> str:
        for message in status.get("messages") or []:
            if message and message[0] == "execution_error":
                details = message[1] if len(message) > 1 else {}
                node = details.get("node_type", "?")
                exception = details.get("exception_message") or details.get("exception_type") or "erro de execucao"
                if _OOM_MARKER in exception.lower():
                    # Recuperacao de OOM sem encerrar processos: apenas ajusta o estado local
                    # (o ComfyUI descarrega TODOS os modelos apos OOM) e orienta o usuario.
                    self._model_warm = False
                    logger.warning("ComfyUI reportou falta de VRAM (job em execucao): estimativa volta a frio.")
                    return (
                        f"VRAM insuficiente no ComfyUI durante {node} (CUDA OOM). Reduza resolucao/steps ou "
                        "libere a memoria do proprio motor (Configuracoes > 'Liberar memoria do motor', que "
                        "so executa com a fila vazia) e tente novamente; se persistir, reinicie o ComfyUI. "
                        "Nenhum processo externo foi encerrado."
                    )
                return f"Falha no ComfyUI ({node}): {exception}"
        return "O ComfyUI reportou erro de execucao no workflow."

    async def _download(self, item: dict) -> tuple[bytes, dict]:
        try:
            response = await self._client.get(
                f"{self.cfg.base_url}/view",
                params={"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")},
                timeout=60.0,
            )
        except httpx.TransportError as exc:
            raise GenerationUnavailableError(f"Falha ao baixar o resultado do ComfyUI: {exc.__class__.__name__}.") from exc
        if response.status_code != 200 or not response.content:
            raise GenerationError("ComfyUI nao retornou a imagem gerada.")
        return response.content, item
