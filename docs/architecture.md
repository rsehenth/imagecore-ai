# Arquitetura

```
Frontend (SPA vanilla servida pelo backend)
   │  fetch/XHR em /api/*
   ▼
FastAPI (backend/main.py — factory, middleware de seguranca, handlers, lock de instancia unica)
   │  Depends -> app.state
   ├─ api/routes_system.py       /api/health, /api/presets (qualidade/samplers/ranges), /api/provider, /api/diagnostics
   ├─ api/routes_references.py   POST/GET/DELETE /api/references (upload streaming)
   ├─ api/routes_generations.py  POST/GET/cancel/DELETE /api/generations, export, composite
   │
   ├─ jobs/manager.py            fila com concorrencia 1 (asyncio.Queue + worker unico),
   │                             progresso no banco, cancelamento cooperativo (asyncio.Event),
   │                             pre-flight de VRAM (nvidia-smi + uso atual para detectar pesos
   │                             residentes no motor), recuperacao de boot
   │
   ├─ generation/                base.py (ImageProvider ABC + excecoes + estimate_vram_mb)
   │                             registry.py (factory por config)
   │                             simulated.py (placeholder Pillow, sem IA)
   │                             comfyui.py (adaptador HTTP: /prompt, /history, /queue, /interrupt,
   │                             /view, /upload/image; workflows t2i e i2i; cancelamento seguro por
   │                             prompt_id; estimativa de VRAM fria/quente)
   │
   └─ storage/                   database.py (SQLite WAL, lock, schema proprio)
                                 gallery.py (geracoes + imagens)
                                 uploads.py (referencias: triagem por magic bytes -> sandbox -> escrita atomica)
                                 image_sandbox.py (decodificacao isolada em subprocesso com Job Object)
                                 safe_fs.py (validacao estrita de caminhos)
   core/                         config.py (YAML + Pydantic + env), presets.py, gpu.py, sandbox.py (Job Object)
```

## Fluxo de uma geracao
1. `POST /api/generations` valida (prompt, estilo, modo t2i/i2i, preset/dimensoes, qualidade/avançados, referencias x capacidades reais do provider) e aplica a politica de resolucao: com perfis nativos (SDXL) a geracao vai para o bucket da proporcao e as dimensoes finais ficam registradas em `export_width/height` + `params.requested`. No modo i2i as dimensoes de geracao derivam da referencia (`i2i_latent_dims`, orcamento ~1 MP, grade de 16 px). Cria linha `generations` (status `queued`) e enfileira.
2. Worker (1 por vez; fila ate 4): marca `running` -> pre-flight (health do motor + VRAM: estimativa fria completa ou apenas incremental quando os pesos estao provavelmente residentes, detectado pelo uso atual da GPU) -> provider.generate com callbacks de progresso e cancelamento.
3. Resultado: arquivo em `data/generations/{id}/{image}.png` + linha `generation_images` (sha256) + status `succeeded`. Falhas e cancelamentos sempre terminam em estado terminal (nunca preso em `running`).
4. Reboot: jobs `running` viram `failed` ("Servidor reiniciado..."); `queued` voltam para a fila.

## Cancelamento
Cooperativo e identificado: `POST /api/generations/{id}/cancel` seta o evento; o provider checa em pontos seguros. No ComfyUI, cada geracao rastreia o proprio `prompt_id`: GET /queue decide a acao — pendente -> `POST /queue {"delete": [prompt_id]}` (remove so o nosso); em execucao e sendo o nosso -> `POST /interrupt`; item em execucao de outro cliente -> nada e feito (limitacao da API publica, sem interrupt por prompt_id; resta uma janela de corrida minima documentada no codigo). `execution_interrupted` no history encerra o job como `cancelled`. Jobs ainda na fila interna sao finalizados como `cancelled` imediatamente.

## Exportacao
- `POST /api/generations/{id}/export` (JSON): re-encode Lanczos (sandbox isolado) para outra resolucao/formato; acima da nativa e upscale declarado.
- `POST /api/generations/{id}/composite` (bytes): composicao imagem+texto montada no cliente (canvas), validada/normalizada pelo mesmo sandbox antes de virar exportacao.

## Exclusao na Galeria

`POST /api/gallery/bulk-delete` recebe IDs de gerações ou de exportações de gerações. O SQLite valida e remove o lote em uma transação, que também registra os arquivos sem referências na fila de limpeza. A limpeza confere os caminhos e o SHA-256 antes de remover cada arquivo. Se um arquivo estiver bloqueado, a fila persiste e o aplicativo tenta processá-la na próxima exclusão ou inicialização.

## Configuracao
`config/settings.yaml` (opcional — defaults no codigo): app (host/porta/log), storage, upload (limites), generation (limites), provider (`simulated`|`comfyui`), comfyui (URL, workflows `workflow`/`workflow_i2i`, timeouts, VRAM estimada, node_map/node_map_i2i, ref_node_map/ref_node_map_i2i), gpu (pre-flight). Overrides: `IMAGECORE_HOST/PORT/PROVIDER/DB_PATH/LAN`.

## Portas
- ImageCore AI: **8765** (loopback por padrao)
- ComfyUI: **8188** (porta local padrão)
