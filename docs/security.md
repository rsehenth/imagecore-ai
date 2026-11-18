# Seguranca e isolamento

## Rede e API local
- Bind padrão em **127.0.0.1:8765**. Para habilitar LAN, use `start.bat --lan` ou configure `IMAGECORE_LAN=1` e `IMAGECORE_HOST` com um IP da interface local. `0.0.0.0` escuta em todas as interfaces IPv4. Em YAML, as opções equivalentes são `app.allow_lan` e `app.host`.
- A API é de usuário único e não exige autenticação. Em LAN, dispositivos da rede podem ler imagens, prompts, projetos e diagnósticos. Use somente uma rede de confiança e mantenha o serviço fora da internet e de proxies reversos.
- Conexões de fora do loopback só podem usar `GET`, `HEAD` e `OPTIONS` no modo LAN. Geração, cancelamento, exclusões, uploads, alterações de projetos, exportações e liberação de VRAM exigem loopback. A inicialização oficial ignora cabeçalhos de proxy ao identificar o cliente.
- Middleware anti DNS-rebinding: Host tem de ser loopback (ou IP privado no modo LAN) -> 403.
- `Sec-Fetch-Site: cross-site` bloqueado. A API valida `Origin` quando presente. Em métodos de escrita, o middleware valida `Referer` quando presente e quando `Origin` está ausente. A comparação exige esquema, host e porta iguais.
- Cabecalhos: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; CSP fora de `/api` (`default-src 'self'; script-src 'self'; img-src 'self' data: blob:`).
- Instancia unica: lock de arquivo + probe da porta antes do uvicorn.

## Uploads (imagens de referencia)
1. Limite de tamanho no streaming (corta mesmo sem Content-Length confiavel) -> 413.
2. **Triagem por magic bytes** (PNG/JPEG/WebP); MIME e nome do cliente sao ignorados para decisoes de seguranca (nome vira apenas rotulo sanitizado).
3. **O processo principal nunca decodifica o arquivo**: um filho `python -I` roda com prazo total e, no Windows, dentro de um Job Object com teto de memoria (KILL_ON_JOB_CLOSE). O filho valida cabecalho/dimensoes/pixels antes do decode (`Image.MAX_IMAGE_PIXELS` — defesa contra bombas de descompressao), recusa animados e truncados, aplica EXIF/ICC e **recodifica a partir dos pixels** — EXIF/GPS/comentarios/bytes anexados nunca sobrevivem.
4. Escritao atomico (tmp + `os.replace`), nome de disco sempre gerado pelo servidor (`uuid4().hex`).
5. Integridade: sha256 conferido a cada leitura/serve -> 409 se o arquivo mudou.

## Caminhos
`backend/storage/safe_fs.py` recusa NUL, UNC, ADS (`:`), `..` e nomes reservados do Windows. Valida a raiz autorizada, usa `lstat` para bloquear links e junções, e remove apenas arquivos regulares sem hard links e diretórios vazios.

## Parametros e IDs
- Pydantic com limites em tudo (prompt, dimensoes, seed, limite de pixels por geracao/exportacao ~17 MP).
- IDs validados por regex `^[0-9a-f]{32}$` — nada de caminho entra por ID.
- Estilos por chave fechada (sem texto livre injetado alem do prompt do usuario, que vai como dado ao workflow, nunca como comando).

## Processos
- Adaptadores usam argv em lista (sem shell), stderr para devnull, `CREATE_NO_WINDOW`.
- O preflight de VRAM pode recusar um job. O script opcional `stop.ps1` identifica processos pelo caminho e pela linha de comando. Em uma instalação compartilhada do ComfyUI, use Ctrl+C no terminal do ImageCore AI para encerrar apenas o backend.
- Sem comandos arbitrarios via parametros de geracao; workflows externos so sao carregados do diretorio `config/workflows/` local.

## Logs e erros
- Logs com metadados (job, provider, dimensoes, contagens) — nunca conteudo de prompt/imagem em excesso nem segredos.
- Handlers de excecao mapeiam dominios para HTTP (400/403/404/409/413/415/422/429/502/503) sem traceback; falhas de decodificador viram recusa generica no filho, sem vazamento.
