# Visao do produto — ImageCore AI

Aplicativo local (Windows 11) de geracao e edicao de imagens por IA, com dois modulos:

## Thumbnail Creator
- Presets de plataforma (dimensoes exatas de exportacao):

| Plataforma | Formato | Dimensoes |
|---|---|---|
| YouTube Thumbnail | 16:9 | 1280x720 |
| YouTube Full HD | 16:9 | 1920x1080 |
| YouTube Shorts | 9:16 | 1080x1920 |
| Instagram Reels | 9:16 | 1080x1920 |
| Instagram Feed | 1:1 | 1080x1080 |
| Instagram Retrato | 4:5 | 1080x1350 |
| Instagram Stories | 9:16 | 1080x1920 |
| TikTok | 9:16 | 1080x1920 |

- Prompt + estilo visual (sufixo acrescentado ao prompt, visivel no historico).
- Imagens de referencia (upload, arrastar-e-soltar, Ctrl+V, remocao).
- **Camada de texto editavel** sobre a imagem (canvas): titulo arrastavel, tamanho/cor/contorno/sombra/fonte. Composta no cliente e registrada como exportacao — o titulo nao depende do modelo generativo.
- Regenerar variacao (nova seed), historico em galeria, exportacao PNG.

## Image Creator
- Proporcoes: 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 21:9 + dimensoes personalizadas.
- Resolucoes por lado curto: 720p, 1080p, 2K (1440p), 4K (2160p), personalizada — dimensoes reais calculadas e exibidas antes de gerar.
- Diferenca clara entre **resolucao de geracao nativa** e **resolucao final**: exportacao acima da nativa e rotulada "upscale (nao e geracao nativa; nao adiciona detalhe real)" na UI.
- Referencias, galeria e exportacao PNG/JPEG via Lanczos (server-side).

## Galerias e historico
SQLite proprio (`data/imagecore.db`): geracoes (modulo, prompt, estilo, seed, dimensoes, motor, estado, etapa, progresso, erro, parametros completos) + imagens (geradas e exportacoes, com sha256) + referencias. Acoes: abrir detalhe, reabrir no studio (parametros restaurados), exportar novamente, selecionar gerações e exportações de gerações para exclusão com confirmação.

## Interface
Desktop-first, tema claro "Mesa de Prova" (papel azul-acinzentado, tinta indigo, rosa para acao e azul para selecao/foco, marcas de corte em volta de cada resultado). Navegacao lateral (trilho de icones abaixo de 1440 px, barra inferior no celular) com 4 modulos: Image Creator, Thumbnail Studio, Galeria (geracoes, projetos editaveis e exportacoes) e Configuracoes (motor, modelos, padroes de geracao, GPU e memoria, preferencias, recursos avancados). Modais para confirmacao/visualizacao/exportacao com foco preso, toasts, progresso na propria area do resultado, estados vazios com proximo passo. Acessibilidade: `:focus-visible`, roles/aria-labels, Escape fecha modais, `prefers-reduced-motion` e preferencia propria de animacoes/densidade.

## Motor de geracao
Camada isolada de providers — a interface nunca depende de um workflow fixo:
- `simulated` (padrao do MVP): placeholder deterministico (Pillow), rotulado "SIMULACAO - SEM IA" na imagem e na UI. Exercita todo o fluxo sem pesos.
- `comfyui`: adaptador real (submit/poll/interrupt/download) para um ComfyUI local, com workflow configuravel em `config/workflows/` e mapeamento de nos por YAML.

Consulte a seção **Model support** do [README](../README.md) para ver o estado de cada perfil.
