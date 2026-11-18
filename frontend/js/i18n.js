/* Internacionalizacao (Etapa 10): pt-BR (padrao) e en-US, com troca imediata sem recarregar.
   Dicionarios em frontend/js/locales/*.js (window.I18N_LOCALES). Regras:
   - t(key, vars): interpola {var} e retorna TEXTO — consumidores usam textContent/setAttribute,
      nunca innerHTML (traducoes nao injetam HTML).
   - tn(key, n, vars): plural por _one/_other (pt-BR e en-US: 1 vs resto); n fica disponivel como {n}.
   - applyDOM(scope): [data-i18n] -> textContent; [data-i18n-attr] com pares atributo:chave
      separados por ponto e virgula -> atributos.
   - stage(raw): os estagios de progresso sao um ENUM ESTAVEL do backend (jobs/manager.py e
      generation/comfyui.py); o mapeamento e por valor exato/prefixo conhecido — texto desconhecido
      (ex.: mensagem externa do ComfyUI) passa integral, sem traducao inventada.
   - error(err): erros da API chegam com `error_code` estavel e independente de idioma (Etapa 10.1,
      backend/main.py `_error_content`); o mapa literal abaixo liga codigo -> chave de dicionario e
      interpola `err.details` (valores estruturados). Sem codigo conhecido (ou chave ausente), a
      MENSAGEM TECNICA ORIGINAL passa integral — fallback seguro, nunca traducao por correspondencia
      de texto. Mensagens externas do ComfyUI e conteudo do usuario nao sao traduzidos.
   - fmtDate: Intl no idioma corrente.
   Persistencia: localStorage imagecore.lang (mesma store imagecore.* das preferencias do app.js).
   Ao trocar: atualiza <html lang>, reaplica o DOM estatico e notifica os modulos via callback +
   evento 'imagecore:langchange' para re-render do que vive em memoria (estado do editor, galeria). */
(() => {
  const LANGS = ['pt-BR', 'en-US'];
  const FALLBACK = 'pt-BR';
  const STORAGE_KEY = 'imagecore.lang';
  let current = FALLBACK;
  const listeners = new Set();

  const dicts = () => window.I18N_LOCALES || {};
  function raw(lang, key) {
    const dict = dicts()[lang] || {};
    if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
    const base = dicts()[FALLBACK] || {};
    return Object.prototype.hasOwnProperty.call(base, key) ? base[key] : undefined;
  }
  function interpolate(text, vars) {
    if (!vars) return text;
    let out = text;
    for (const [name, value] of Object.entries(vars)) out = out.split(`{${name}}`).join(value === undefined || value === null ? '' : String(value));
    return out;
  }
  function t(key, vars) {
    const text = raw(current, key);
    if (text === undefined) return key; // falta de traducao fica visivel, nunca quebra a UI
    return interpolate(text, vars);
  }
  function tn(key, n, vars) {
    const count = Number(n) || 0;
    const rule = count === 1 ? 'one' : 'other';
    const text = raw(current, `${key}_${rule}`);
    if (text === undefined) return t(key, vars); // sem par plural: usa a chave simples
    return interpolate(text, { ...(vars || {}), n: count });
  }

  function applyAttr(el, spec) {
    for (const pair of spec.split(';')) {
      const [attr, key] = pair.split(':').map((part) => part.trim());
      if (attr && key) el.setAttribute(attr, t(key));
    }
  }
  function applyDOM(scope) {
    const root = scope || document;
    for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const el of root.querySelectorAll('[data-i18n-attr]')) applyAttr(el, el.dataset.i18nAttr);
  }

  function setLanguage(lang) {
    if (!LANGS.includes(lang)) lang = FALLBACK;
    current = lang;
    try { window.localStorage.setItem(STORAGE_KEY, lang); } catch (err) { /* sem persistencia: vale na sessao */ }
    document.documentElement.lang = lang;
    applyDOM(document);
    for (const fn of listeners) { try { fn(lang); } catch (err) { /* um modulo quebrado nao trava os demais */ } }
    document.dispatchEvent(new CustomEvent('imagecore:langchange', { detail: { lang } }));
  }
  function onChange(fn) { listeners.add(fn); }

  /* Enum de estagios do backend (verificar/gerar/cancelar). 'vram ok (...)' e 'cancelando: ...'
     variam o sufixo: casam por prefixo. Desconhecido: texto bruto (limitacao documentada). */
  const STAGE_EXACT = {
    'verificando motor': 'stage.checkingEngine',
    'preparando workflow': 'stage.preparingWorkflow',
    'enviando ao ComfyUI': 'stage.sending',
    'na fila do ComfyUI': 'stage.queued',
    'aguardando fila do ComfyUI': 'stage.awaitingQueue',
    'executando no ComfyUI': 'stage.executing',
    'amostrando': 'stage.sampling',
    'baixando resultado': 'stage.downloading',
    'preparando referencia': 'stage.preparingReference',
  };
  const STAGE_PREFIX = [
    ['vram ok', 'stage.vramOk'],
    ['cancelando', 'stage.cancelling'],
  ];
  function stage(value) {
    if (!value) return '';
    const key = STAGE_EXACT[value] || (STAGE_PREFIX.find(([prefix]) => value.startsWith(prefix)) || [])[1];
    return key ? t(key) : value;
  }

  /* Codigos de erro estaveis do backend (Etapa 10.1) -> chaves de dicionario.
     Codigo ausente do mapa ou chave sem traducao: mensagem tecnica original (fallback). */
  const ERROR_KEYS = {
    // validacao do pedido (geracao)
    prompt_required: 'error.promptRequired',
    prompt_too_long: 'error.promptTooLong',
    unknown_style: 'error.unknownStyle',
    unknown_profile: 'error.unknownProfile',
    invalid_steps: 'error.invalidSteps',
    invalid_cfg: 'error.invalidCfg',
    sampler_fixed: 'error.samplerFixed',
    negative_not_supported: 'error.negativeNotSupported',
    unknown_ratio: 'error.unknownRatio',
    unknown_resolution: 'error.unknownResolution',
    unknown_sampler: 'error.unknownSampler',
    unknown_preset: 'error.unknownPreset',
    missing_dimensions: 'error.missingDimensions',
    resolution_limit_exceeded: 'error.resolutionLimitExceeded',
    export_limit_exceeded: 'error.exportLimitExceeded',
    invalid_denoise: 'error.invalidDenoise',
    seed_required: 'error.seedRequired',
    too_many_references: 'error.tooManyReferences',
    i2i_unsupported: 'error.i2iUnsupported',
    i2i_requires_reference: 'error.i2iRequiresReference',
    references_unsupported: 'error.referencesUnsupported',
    invalid_dimensions: 'error.invalidDimensions',
    // perfis e recusas de geracao
    simulated_profile_rejected: 'error.simulatedProfileRejected',
    profile_not_installed: 'error.profileNotInstalled',
    active_workflow_mismatch: 'error.activeWorkflowMismatch',
    profile_not_runnable: 'error.profileNotRunnable',
    model_mismatch: 'error.modelMismatch',
    workflow_missing: 'error.workflowMissing',
    workflow_invalid: 'error.workflowInvalid',
    i2i_not_configured: 'error.i2iNotConfigured',
    // cancelamento e ciclo de vida
    generation_not_found: 'error.generationNotFound',
    generation_not_running: 'error.generationNotRunning',
    generation_id_invalid: 'error.generationIdInvalid',
    queue_full: 'error.queueFull',
    manager_not_running: 'error.managerNotRunning',
    // imagens, projetos e exportacao
    image_id_invalid: 'error.imageIdInvalid',
    image_not_found: 'error.imageNotFound',
    image_file_invalid: 'error.imageFileInvalid',
    image_file_missing: 'error.imageFileMissing',
    image_integrity: 'error.imageIntegrity',
    composition_too_large: 'error.compositionTooLarge',
    invalid_composition: 'error.invalidComposition',
    invalid_preview: 'error.invalidPreview',
    linked_generation_not_found: 'error.linkedGenerationNotFound',
    linked_reference_not_found: 'error.linkedReferenceNotFound',
    project_not_found: 'error.projectNotFound',
    project_id_invalid: 'error.projectIdInvalid',
    invalid_layers: 'error.invalidLayers',
    too_many_layers: 'error.tooManyLayers',
    invalid_layer: 'error.invalidLayer',
    layer_too_large: 'error.layerTooLarge',
    invalid_field: 'error.invalidField',
    field_too_large: 'error.fieldTooLarge',
    invalid_image_kind: 'error.invalidImageKind',
    // referencias e sandbox de imagem
    reference_id_invalid: 'error.referenceIdInvalid',
    reference_empty: 'error.referenceEmpty',
    reference_too_large: 'error.referenceTooLarge',
    reference_invalid_image: 'error.referenceInvalidImage',
    reference_limit_reached: 'error.referenceLimitReached',
    reference_not_found: 'error.referenceNotFound',
    reference_path_invalid: 'error.referencePathInvalid',
    reference_integrity: 'error.referenceIntegrity',
    image_dimensions_limit: 'error.imageDimensionsLimit',
    image_format_unsupported: 'error.imageFormatUnsupported',
    image_animated_unsupported: 'error.imageAnimatedUnsupported',
    image_invalid: 'error.imageInvalid',
    sandbox_unavailable: 'error.sandboxUnavailable',
    sandbox_timeout: 'error.sandboxTimeout',
    sandbox_resources: 'error.sandboxResources',
    sandbox_output_too_large: 'error.sandboxOutputTooLarge',
    // motor e memoria
    engine_unavailable: 'error.engineUnavailable',
    vram_insufficient: 'error.vramInsufficient',
    generation_failed: 'error.generationFailed',
    free_not_supported: 'error.freeNotSupported',
    free_not_dedicated: 'error.freeNotDedicated',
    free_missing_confirm: 'error.freeMissingConfirm',
    free_queue_not_empty: 'error.freeQueueNotEmpty',
    free_queue_unverifiable: 'error.freeQueueUnverifiable',
    free_refused: 'error.freeRefused',
    // seguranca e configuracao
    lan_only: 'error.lanOnly',
    lan_read_only: 'error.lanReadOnly',
    local_only: 'error.localOnly',
    host_unauthorized: 'error.hostUnauthorized',
    cross_site_blocked: 'error.crossSiteBlocked',
    origin_unauthorized: 'error.originUnauthorized',
    origin_blocked: 'error.originBlocked',
    referer_unauthorized: 'error.refererUnauthorized',
    config_invalid: 'error.configInvalid',
    gallery_invalid_selection: 'error.galleryInvalidSelection',
    gallery_generation_active: 'error.galleryGenerationActive',
    gallery_project_dependency: 'error.galleryProjectDependency',
    gallery_file_unsafe: 'error.galleryFileUnsafe',
    gallery_delete_failed: 'error.galleryDeleteFailed',
  };
  function error(err) {
    if (!err) return '';
    const key = err.code && ERROR_KEYS[err.code];
    if (key) {
      const text = raw(current, key);
      if (text !== undefined) return interpolate(text, err.details);
    }
    return err.message || String(err); // fallback seguro: mensagem tecnica original
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(current, { dateStyle: 'short', timeStyle: 'short' }); } catch (err) { return iso; }
  }
  function fmtNum(n) {
    try { return new Intl.NumberFormat(current).format(n); } catch (err) { return String(n); }
  }

  function init() {
    let saved = null;
    try { saved = window.localStorage.getItem(STORAGE_KEY); } catch (err) { /* sem storage */ }
    current = LANGS.includes(saved) ? saved : FALLBACK;
    document.documentElement.lang = current;
    applyDOM(document);
    const select = document.getElementById('pref-language');
    if (select) {
      select.value = current;
      select.addEventListener('change', () => setLanguage(select.value));
    }
  }

  window.i18n = { t, tn, applyDOM, setLanguage, getLanguage: () => current, onChange, stage, error, fmtDate, fmtNum, LANGS };
  init();
})();
