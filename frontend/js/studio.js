/* Studio (Thumbnail Creator e Image Creator): formulario, referencias, jobs, preview,
   camada de texto em canvas e exportacao. Duas instancias com prefixos t- / i-. */
(() => {
  const TIERS = { '720p': 720, '1080p': 1080, '2k': 1440, '4k': 2160 };
  const MAX_REFS = 4;
  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  /* Etapa 7: perfis de modelo compartilhados entre os dois studios (uma busca so). */
  let profilesCache = null;
  function loadProfiles() {
    if (!profilesCache) {
      profilesCache = api.get('/api/profiles').then((data) => data.profiles || []).catch(() => []);
    }
    return profilesCache;
  }
  /* Buckets nativos do SDXL (espelham backend/core/presets.py) — a geração acontece
     no bucket; as dimensões finais exatas são alcançadas na exportação. */
  const SDXL_BUCKETS = [
    [1024, 1024], [1152, 896], [1216, 832], [1344, 768], [1536, 640],
    [896, 1152], [832, 1216], [768, 1344], [640, 1536],
  ];
  function bucketFor(width, height) {
    const target = width / height;
    return SDXL_BUCKETS.reduce((best, bucket) => {
      const d = Math.abs(bucket[0] / bucket[1] - target) - Math.abs(best[0] / best[1] - target);
      return d < -1e-9 || (Math.abs(d) <= 1e-9 && bucket[0] * bucket[1] < best[0] * best[1]) ? bucket : best;
    });
  }

  function dimsForRatio(ratio, shortSide) {
    const [rw, rh] = ratio.split(':').map(Number);
    if (rw >= rh) return [Math.round((shortSide * rw) / rh), shortSide];
    return [shortSide, Math.round((shortSide * rh) / rw)];
  }
  function dimsForTier(ratio, tier) { return dimsForRatio(ratio, TIERS[tier] || 1080); }
  function triggerDownload(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /* ------------------------------------------------ dialogo de exportacao (compartilhado) */
  const exportModal = document.getElementById('export-modal');
  const exportTier = document.getElementById('export-tier');
  const exportFormat = document.getElementById('export-format');
  const exportNote = document.getElementById('export-note');
  const exportOk = document.getElementById('export-ok');
  const exportQuality = document.getElementById('export-quality');
  const exportQualityVal = document.getElementById('export-qualityval');
  const exportQualityField = document.getElementById('export-qualityfield');
  let exportCtx = null;

  function tierDims(tier, w, h) {
    if (tier === 'original') return [w, h];
    const scale = (TIERS[tier] || h) / Math.min(w, h);
    return [Math.round(w * scale), Math.round(h * scale)];
  }
  function updateExportNote() {
    if (!exportCtx) return;
    const [w, h] = tierDims(exportTier.value, exportCtx.baseDims[0], exportCtx.baseDims[1]);
    const upscale = w * h > exportCtx.baseDims[0] * exportCtx.baseDims[1];
    const qualityNote = exportFormat.value === 'jpeg' ? i18n.t('export.jpegQualitySuffix', { q: exportQuality.value }) : '';
    exportNote.textContent = i18n.t('export.output', { dims: `${w}×${h}` }) + qualityNote
      + (upscale ? i18n.t('export.upscaleNote') : '');
  }
  exportTier.addEventListener('change', updateExportNote);
  exportFormat.addEventListener('change', () => { exportQualityField.hidden = exportFormat.value !== 'jpeg'; updateExportNote(); });
  exportQuality.addEventListener('input', () => { exportQualityVal.textContent = exportQuality.value; updateExportNote(); });
  exportOk.addEventListener('click', async () => {
    if (!exportCtx) return;
    const [w, h] = tierDims(exportTier.value, exportCtx.baseDims[0], exportCtx.baseDims[1]);
    exportOk.disabled = true;
    try {
      await exportCtx.onExport({ tier: exportTier.value, format: exportFormat.value, quality: Number(exportQuality.value), width: w, height: h });
      ui.closeModal(exportModal);
    } catch (err) {
      ui.showToast(i18n.error(err), 'error');
    } finally {
      exportOk.disabled = false;
    }
  });
  function openExportDialog({ baseDims, allowFormat, onExport }) {
    exportCtx = { baseDims, allowFormat, onExport };
    exportFormat.closest('.field').hidden = !allowFormat;
    exportFormat.disabled = !allowFormat;
    if (!allowFormat) exportFormat.value = 'png';
    exportQualityField.hidden = exportFormat.value !== 'jpeg';
    exportTier.value = 'original';
    updateExportNote();
    ui.openModal(exportModal);
  }
  window.exporter = { openExportDialog, tierDims };

  /* Buckets de rotulos por CHAVE estavel do backend (presets.py). Chave nova do backend
     cai no label original (pt-BR) em vez de quebrar. */
  const QUALITY_LABEL_KEYS = { rapida: 'quality.rapida', padrao: 'quality.padrao', alta: 'quality.alta' };
  const STYLE_LABEL_KEYS = {
    none: 'style.none', photoreal: 'style.photoreal', illustration: 'style.illustration',
    render3d: 'style.render3d', anime: 'style.anime', minimal: 'style.minimal', cinematic: 'style.cinematic',
  };
  const PLATFORM_LABEL_KEYS = {
    youtube_thumb: 'platform.youtube_thumb', youtube_fhd: 'platform.youtube_fhd', youtube_shorts: 'platform.youtube_shorts',
    instagram_reels: 'platform.instagram_reels', instagram_feed: 'platform.instagram_feed', instagram_portrait: 'platform.instagram_portrait',
    instagram_stories: 'platform.instagram_stories', tiktok: 'platform.tiktok',
  };
  const presetLabelText = (preset) => (PLATFORM_LABEL_KEYS[preset.key] ? i18n.t(PLATFORM_LABEL_KEYS[preset.key]) : preset.label);
  const styleLabelText = (style) => (STYLE_LABEL_KEYS[style.key] ? i18n.t(STYLE_LABEL_KEYS[style.key]) : style.label);

  /* ------------------------------------------------ fabrica do studio */
  function createStudio({ prefix, mode }) {
    const $ = (suffix) => document.getElementById(`${prefix}-${suffix}`);
    const els = {
      form: $('form'), dims: $('dims'), prompt: $('prompt'), style: $('style'),
      drop: $('drop'), file: $('file'), refs: $('refs'), seed: $('seed'), seedRand: $('seedrand'),
      seedRow: $('seedrow'), seedEffect: $('seedeffect'),
      generate: $('generate'), progress: $('progress'), cancel: $('cancel'), note: $('note'),
      stage: $('stage'), empty: $('empty'), actions: $('actions'), regen: $('regen'),
      variations: $('variations'), export: $('export'),
      // modo de geracao, denoise, qualidade e opcoes avancadas
      denoisefield: $('denoisefield'), denoise: $('denoise'), denoiseVal: $('denoiseval'),
      quality: $('quality'), advanced: $('advanced'), steps: $('steps'), cfg: $('cfg'), sampler: $('sampler'),
      negative: $('negative'), langhint: $('langhint'),
      // Etapa 7: perfil de modelo (estado honesto por perfil; candidatos ficam desabilitados)
      profile: document.getElementById(`${prefix}-profile`),
      profileHint: document.getElementById(`${prefix}-profile-hint`),
      // comparison i2i (apenas image studio possui os elementos)
      refcompare: document.getElementById(`${prefix}-refcompare`),
      refimg: document.getElementById(`${prefix}-refimg`),
      // thumbnail: select de preset | image: select de proporcao
      preset: document.getElementById(`${prefix}-preset`) || document.getElementById(`${prefix}-ratio`),
    };
    const resolutionBox = document.getElementById(`${prefix}-resolution`);
    /* Etapa 5: o editor de camadas (editor.js) substitui a antiga camada de texto unica.
       O studio cuida de geracao, presets, projetos e exportacao; o editor, de camadas. */
    const state = {
      refs: new Map(), job: null, pollTimer: null, busy: false,
      currentGen: null, variations: [], providerCaps: {}, lastRef: null,
      projectId: null, loadedParams: null,
    };
    let editor = null;
    let data = { platform_presets: [], aspect_ratios: [], styles: [] };

    /* ------------------------------------------------------------ inicializacao */
    async function init() {
      try { data = await api.get('/api/presets'); } catch (err) { ui.showToast(i18n.t('studio.presetsFailed'), 'error'); }
      if (mode === 'thumbnail') {
        editor = createEditor({
          root: document.getElementById('t-editor'),
          toolbar: document.getElementById('t-toolbar'),
          canvas: document.getElementById('t-canvas'),
          stage: els.stage,
          empty: els.empty,
          dims: () => currentDims() || [1280, 720],
          presetKey: () => els.preset.value,
          urls: { image: api.imageUrl, reference: api.referenceUrl },
          toast: ui.showToast,
          onSave: saveProject,
        });
        const platforms = [...new Set(data.platform_presets.map((p) => p.platform))];
        for (const platform of platforms) {
          const group = document.createElement('optgroup');
          group.label = platform;
          for (const preset of data.platform_presets.filter((p) => p.platform === platform)) {
            const option = document.createElement('option');
            option.value = preset.key;
            option.textContent = `${presetLabelText(preset)} (${preset.ratio})`;
            group.appendChild(option);
          }
          els.preset.appendChild(group);
        }
        els.preset.addEventListener('change', updateDims);
        wireLocalBackground();
        wireProjects();
        refreshProjects();
      } else {
        for (const ratio of data.aspect_ratios) {
          const option = document.createElement('option');
          option.value = ratio.key;
          option.textContent = ratio.key;
          els.preset.appendChild(option); // no modo image, els.preset e o select de proporcao
        }
        els.preset.addEventListener('change', updateDims);
        document.getElementById('i-tier').addEventListener('change', () => {
          document.getElementById('i-customdims').hidden = document.getElementById('i-tier').value !== 'custom';
          updateDims();
        });
        document.getElementById('i-width').addEventListener('input', updateDims);
        document.getElementById('i-height').addEventListener('input', updateDims);
      }
      for (const style of data.styles) {
        const option = document.createElement('option');
        option.value = style.key;
        option.textContent = styleLabelText(style);
        els.style.appendChild(option);
      }
      wireQualityControls();
      wireGenMode();
      updateDims();
      wireUpload();
      wireProfiles();
      els.form.addEventListener('submit', (event) => { event.preventDefault(); generate(false); });
      els.cancel.addEventListener('click', cancelJob);
      // Ctrl/Cmd + Enter na descricao gera (atalho de quem escreve prompts longos).
      els.prompt.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); els.form.requestSubmit(); }
      });
      els.regen.addEventListener('click', () => generate(true));
      els.export.addEventListener('click', () => {
        if (mode === 'thumbnail') {
          if (!editor || !editor.hasBackground()) {
            ui.showToast(i18n.t('studio.exportNeedsBackground'), 'error');
            return;
          }
          // A composicao usa as dimensoes finais do preset (export_width/height), mesmo
          // quando a geracao aconteceu em bucket nativo SDXL.
          const dims = currentDims() || [1280, 720];
          openExportDialog({ baseDims: dims, allowFormat: true, onExport: exportComposite });
          return;
        }
        if (!state.currentGen) return;
        const gen = state.currentGen;
        openExportDialog({
          baseDims: [gen.width, gen.height],
          allowFormat: true,
          onExport: exportResize,
        });
      });
      els.seedRand.addEventListener('click', () => { els.seed.value = String(Math.floor(Math.random() * 4294967296)); });
      for (const radio of document.querySelectorAll(`input[name="${prefix}-seedmode"]`)) {
        radio.addEventListener('change', () => { if (radio.checked) setSeedMode(radio.value); });
      }
      setSeedMode('auto');
      loadProviderNote();
    }

    /* ------------------------------------------------------------ modo de geracao / qualidade */
    function genModeValue() {
      const checked = document.querySelector(`input[name="${prefix}-genmode"]:checked`);
      return checked ? checked.value : 't2i';
    }
    function setGenMode(value) {
      const radio = document.querySelector(`input[name="${prefix}-genmode"][value="${value}"]`);
      if (!radio) return;
      radio.checked = true;
      els.denoisefield.hidden = value !== 'i2i';
      if (resolutionBox) resolutionBox.hidden = value === 'i2i'; // i2i: dimensoes vem da referencia
      updateDims();
    }
    function wireGenMode() {
      for (const radio of document.querySelectorAll(`input[name="${prefix}-genmode"]`)) {
        radio.addEventListener('change', () => { if (radio.checked) setGenMode(radio.value); });
      }
      els.denoise.addEventListener('input', () => { els.denoiseVal.textContent = Number(els.denoise.value).toFixed(2); });
      if (els.prompt && els.langhint) {
        // Dica honesta de aderencia (sem traducao automatica): SDXL e treinado em ingles.
        els.prompt.addEventListener('input', () => {
          els.langhint.hidden = !/[\u00C0-\u024F\u1E00-\u1EFF]/.test(els.prompt.value);
        });
      }
    }
    /* Etapa 7: perfis de modelo. O estado vem do servidor (instalado/compativel/indisponivel/
       nao validado); candidatos ficam desabilitados com o motivo, nunca selecionaveis.
       Etapa 10.1: o motivo e traduzido pelo `status_code` estavel (fallback: texto bruto). */
    function wireProfiles() {
      if (!els.profile) return;
      loadProfiles().then((profiles) => {
        if (!profiles.length) return;
        els.profile.textContent = '';
        const STATUS_KEYS = { instalado: 'profile.instalado', compativel: 'profile.compativel', nao_validado: 'profile.nao_validado', indisponivel: 'profile.indisponivel' };
        const REASON_KEYS = {
          workflow_missing: 'profile.reasonWorkflowMissing',
          weights_unverified: 'profile.reasonWeightsUnverified',
          weights_missing: 'profile.reasonWeightsMissing',
          smoke_pending: 'profile.reasonSmokePending',
          installed_ok: 'profile.reasonInstalledOk',
        };
        const reasonText = (profile) => {
          const key = REASON_KEYS[profile.status_code];
          return key ? i18n.t(key, { files: (profile.missing_files || []).join(', ') }) : (profile.status_reason || '');
        };
        for (const profile of profiles) {
          const option = document.createElement('option');
          option.value = profile.id;
          option.textContent = profile.status === 'instalado'
            ? profile.label
            : i18n.t('studio.profilePending', {
              label: profile.label,
              status: STATUS_KEYS[profile.status] ? i18n.t(STATUS_KEYS[profile.status]).toLowerCase() : profile.status_label.toLowerCase(),
            });
          option.disabled = profile.status !== 'instalado';
          option.title = reasonText(profile);
          els.profile.appendChild(option);
        }
        els.profile.value = 'sdxl';
        updateProfileHint();
        els.profile.addEventListener('change', updateProfileHint);
      });
    }
    function updateProfileHint() {
      if (!els.profile || !els.profileHint) return;
      loadProfiles().then((profiles) => {
        const active = profiles.find((p) => p.id === els.profile.value);
        if (active) {
          const key = {
            workflow_missing: 'profile.reasonWorkflowMissing',
            weights_unverified: 'profile.reasonWeightsUnverified',
            weights_missing: 'profile.reasonWeightsMissing',
            smoke_pending: 'profile.reasonSmokePending',
            installed_ok: 'profile.reasonInstalledOk',
          }[active.status_code];
          els.profileHint.textContent = active.notes || (key ? i18n.t(key, { files: (active.missing_files || []).join(', ') }) : (active.status_reason || ''));
        }
      });
    }
    function wireQualityControls() {
      for (const preset of data.quality_presets || []) {
        const option = document.createElement('option');
        option.value = preset.key;
        option.textContent = QUALITY_LABEL_KEYS[preset.key] ? i18n.t(QUALITY_LABEL_KEYS[preset.key], { steps: preset.steps, cfg: preset.cfg }) : preset.label;
        if (preset.key === 'padrao') option.selected = true;
        els.quality.appendChild(option);
      }
      const ranges = data.ranges || {};
      if (ranges.steps) { els.steps.min = ranges.steps[0]; els.steps.max = ranges.steps[1]; }
      if (ranges.cfg) { els.cfg.min = ranges.cfg[0]; els.cfg.max = ranges.cfg[1]; }
      const auto = document.createElement('option');
      auto.value = '';
      auto.textContent = i18n.t('studio.samplerAuto');
      els.sampler.appendChild(auto);
      for (const sampler of data.samplers || []) {
        const option = document.createElement('option');
        option.value = sampler;
        option.textContent = sampler;
        els.sampler.appendChild(option);
      }
    }

    /* Etapa 10: o texto do note depende so de caps em cache — reuso na troca de idioma. */
    function providerNoteText(info) {
      const caps = info.capabilities || {};
      const label = info.name === 'simulated'
        ? i18n.t('provider.simulatedStudio')
        : i18n.t('provider.engineName', { name: info.name });
      const model = caps.model ? i18n.t('provider.modelSuffix', { model: caps.model }) : '';
      return `${label}${model}. ${caps.note || ''}`.trim();
    }
    async function loadProviderNote() {
      try {
        const info = await api.get('/api/provider');
        state.providerCaps = info.capabilities || {};
        state.providerInfo = info;
        els.note.textContent = providerNoteText(info);
        if (els.negative && els.negative.closest('.field')) {
          // O negative prompt so e exibido quando o motor realmente o aceita.
          els.negative.closest('.field').hidden = !state.providerCaps.negative_prompt_supported;
        }
        updateDims();
      } catch (err) { els.note.textContent = i18n.t('provider.offlineNote'); }
    }

    /* ------------------------------------------------------------ dimensoes */
    function currentDims() {
      if (mode === 'thumbnail') {
        const preset = data.platform_presets.find((p) => p.key === els.preset.value);
        return preset ? [preset.width, preset.height] : null;
      }
      const tier = document.getElementById('i-tier').value;
      if (tier === 'custom') {
        const w = parseInt(document.getElementById('i-width').value, 10);
        const h = parseInt(document.getElementById('i-height').value, 10);
        if (!w || !h) return null;
        return [w, h];
      }
      return dimsForTier(els.preset.value, tier);
    }
    function updateDims() {
      if (genModeValue() === 'i2i') {
        els.dims.textContent = i18n.t('studio.dimsI2i');
        if (editor) editor.setDims();
        return;
      }
      const dims = currentDims();
      if (!dims) { els.dims.textContent = '—'; if (editor) editor.setDims(); return; }
      if (state.providerCaps.native_profiles) {
        const [bw, bh] = bucketFor(dims[0], dims[1]);
        if (bw !== dims[0] || bh !== dims[1]) {
          els.dims.textContent = i18n.t('studio.dimsBucket', { w: dims[0], h: dims[1], bw, bh });
          if (editor) editor.setDims();
          return;
        }
      }
      els.dims.textContent = i18n.t('studio.dimsPx', { w: dims[0], h: dims[1] });
      if (editor) editor.setDims();
    }

    /* ------------------------------------------------------------ seed (auto/manual) */
    function seedMode() {
      const checked = document.querySelector(`input[name="${prefix}-seedmode"]:checked`);
      return checked ? checked.value : 'auto';
    }
    function setSeedMode(value) {
      const radio = document.querySelector(`input[name="${prefix}-seedmode"][value="${value}"]`);
      if (!radio) return;
      radio.checked = true;
      els.seedRow.hidden = value !== 'manual';
      if (value === 'manual' && !els.seed.value.trim()) {
        // Preenche com a seed da imagem em tela (reuso) ou sorteia uma inicial.
        els.seed.value = String(state.currentGen ? state.currentGen.seed : Math.floor(Math.random() * 4294967296));
      }
    }
    function showEffectiveSeed(gen) {
      if (!gen || gen.seed === null || gen.seed === undefined) {
        els.seedEffect.hidden = true;
        els.seedEffect.textContent = '';
        return;
      }
      const used = (gen.params && gen.params.seed_mode) === 'manual' ? i18n.t('gallery.seedManual') : i18n.t('gallery.seedAuto');
      els.seedEffect.textContent = i18n.t('studio.seedEffect', { seed: gen.seed, mode: used });
      els.seedEffect.hidden = false;
    }

    /* ------------------------------------------------------------ referencias */
    function wireUpload() {
      els.drop.addEventListener('click', () => els.file.click());
      els.drop.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') els.file.click(); });
      els.file.addEventListener('change', () => { addFiles(els.file.files); els.file.value = ''; });
      for (const ev of ['dragenter', 'dragover']) {
        els.drop.addEventListener(ev, (event) => { event.preventDefault(); els.drop.classList.add('over'); });
      }
      for (const ev of ['dragleave', 'drop']) {
        els.drop.addEventListener(ev, (event) => { event.preventDefault(); els.drop.classList.remove('over'); });
      }
      els.drop.addEventListener('drop', (event) => { if (event.dataTransfer && event.dataTransfer.files.length) addFiles(event.dataTransfer.files); });
      document.addEventListener('paste', (event) => {
        const view = document.getElementById(`view-${mode}`);
        if (!view || view.hidden) return;
        const files = [...(event.clipboardData?.items || [])].filter((item) => item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
        if (files.length) { event.preventDefault(); addFiles(files); }
      });
    }
    async function addFiles(fileList) {
      const limit = genModeValue() === 'i2i' ? 1 : MAX_REFS; // i2i usa exatamente 1 referencia
      const room = limit - state.refs.size;
      const files = [...fileList].filter((f) => f.type.startsWith('image/')).slice(0, Math.max(0, room));
      if (!files.length) {
        if (room <= 0) ui.showToast(genModeValue() === 'i2i' ? i18n.t('studio.i2iSingleRef') : i18n.t('studio.maxRefs', { n: MAX_REFS }), 'error');
        return;
      }
      for (const file of files) {
        try {
          const result = await api.uploadFile('/api/references', file);
          state.refs.set(result.reference.id, result.reference);
          renderRefs();
        } catch (err) { ui.showToast(i18n.error(err), 'error'); }
      }
    }
    function renderRefs() {
      els.refs.textContent = '';
      for (const [id, row] of state.refs) {
        const card = document.createElement('div');
        card.className = 'att-preview';
        const img = document.createElement('img');
        img.src = api.referenceUrl(id);
        img.alt = row.display_name;
        img.title = `${row.display_name} · ${row.width}×${row.height}`;
        img.addEventListener('click', () => ui.openLightbox({ url: img.src, title: row.display_name, meta: `${row.width}×${row.height} · ${ui.fmtBytes(row.size_bytes)}` }));
        const name = document.createElement('span');
        name.className = 'att-name';
        name.textContent = row.display_name;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'att-remove';
        remove.setAttribute('aria-label', i18n.t('studio.removeRefAria', { name: row.display_name }));
        remove.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-x"/></svg>';
        remove.addEventListener('click', async () => {
          state.refs.delete(id);
          renderRefs();
          try { await api.del(`/api/references/${id}`); } catch (err) { /* registro ja removido localmente */ }
        });
        card.append(img, name, remove);
        els.refs.appendChild(card);
      }
    }

    /* ------------------------------------------------------------ geracao */
    async function generate(isVariation) {
      if (state.busy) { ui.showToast(i18n.t('studio.busy'), 'error'); return; }
      const prompt = els.prompt.value.trim();
      if (!prompt) { ui.showToast(i18n.t('studio.needPrompt'), 'error'); els.prompt.focus(); return; }
      const genMode = genModeValue();
      if (genMode === 'i2i') {
        if (state.refs.size !== 1) {
          ui.showToast(i18n.t('studio.i2iNeedsRef'), 'error');
          return;
        }
      } else {
        const dims = currentDims();
        if (!dims) { ui.showToast(i18n.t('studio.invalidDims'), 'error'); return; }
      }
      const body = {
        module: mode,
        prompt,
        style: els.style.value,
        mode: genMode,
        quality: els.quality.value,
        reference_ids: [...state.refs.keys()],
        seed_mode: seedMode(),
        profile: els.profile && els.profile.value ? els.profile.value : 'sdxl',
      };
      if (els.negative && els.negative.value.trim()) body.negative_prompt = els.negative.value.trim();
      if (genMode === 'i2i') {
        body.denoise = Number(els.denoise.value);
        state.lastRef = [...state.refs.values()][0] || null; // para comparar resultado x referencia
      }
      const steps = parseInt(els.steps.value, 10);
      if (!Number.isNaN(steps)) body.steps = steps;
      const cfg = parseFloat(els.cfg.value);
      if (!Number.isNaN(cfg)) body.cfg = cfg;
      if (els.sampler.value) body.sampler = els.sampler.value;
      if (body.seed_mode === 'manual') {
        const raw = els.seed.value.trim();
        const parsed = Number(raw);
        if (raw === '' || !Number.isInteger(parsed) || parsed < 0 || parsed > 4294967295) {
          ui.showToast(i18n.t('studio.invalidSeed'), 'error');
          els.seed.focus();
          return;
        }
        body.seed = parsed;
      }
      if (genMode !== 'i2i') {
        if (mode === 'thumbnail') body.preset = els.preset.value;
        else {
          body.ratio = els.preset.value;
          const tier = document.getElementById('i-tier').value;
          if (tier === 'custom') { body.width = parseInt(document.getElementById('i-width').value, 10); body.height = parseInt(document.getElementById('i-height').value, 10); }
          else body.tier = tier;
        }
      } else if (mode === 'thumbnail') {
        body.preset = els.preset.value; // define as dimensoes finais da exportacao composta
      }
      setBusy(true);
      updateProgress(0, i18n.t('stage.queueing'));
      try {
        const result = await api.post('/api/generations', body);
        state.job = result.generation;
        startPolling();
      } catch (err) {
        setBusy(false);
        ui.showToast(i18n.error(err), 'error');
      }
    }
    function startPolling() {
      stopPolling();
      state.pollTimer = setInterval(async () => {
        if (!state.job) return;
        try {
          const result = await api.get(`/api/generations/${state.job.id}`);
          const gen = result.generation;
          updateProgress(gen.progress, gen.stage ? i18n.stage(gen.stage) : '—');
          if (['succeeded', 'failed', 'cancelled'].includes(gen.status)) {
            stopPolling();
            onTerminal(gen);
          }
        } catch (err) { /* rede instável: mantém tentando */ }
      }, 700);
    }
    function stopPolling() { clearInterval(state.pollTimer); state.pollTimer = null; }
    function updateProgress(pct, stage) {
      els.progress.hidden = false;
      els.progress.querySelector('.progress-fill').style.width = `${clamp(pct, 0, 100)}%`;
      els.progress.querySelector('.stage').textContent = `${stage}…`;
      els.progress.querySelector('.pct').textContent = `${Math.round(pct)}%`;
    }
    function setBusy(busy) {
      state.busy = busy;
      els.generate.disabled = busy;
      els.stage.classList.toggle('is-busy', busy);
      if (busy) els.note.classList.remove('is-error');
      if (!busy) els.progress.hidden = true;
    }
    async function onTerminal(gen) {
      state.job = null;
      setBusy(false);
      if (gen.status === 'succeeded') { renderResult(gen); return; }
      if (gen.status === 'failed') {
        const error = gen.error || i18n.t('studio.unknownError');
        els.note.textContent = i18n.t('studio.lastFailure', { error });
        els.note.classList.add('is-error');
        ui.showToast(i18n.t('studio.genFailedToast', { error }), 'error');
        return;
      }
      // Cancelamento (Etapa 8): com a API individual o ComfyUI interrompe o prompt proprio
      // de forma atomica; sem ela, o processamento atual termina e o resultado e descartado.
      ui.showToast(i18n.t('studio.cancelledToast'), 'ok');
    }
    async function cancelJob() {
      if (!state.job) return;
      try {
        await api.post(`/api/generations/${state.job.id}/cancel`);
        // Etapa 8: o ComfyUI 0.45+ tem cancelamento INDIVIDUAL por job (interrupt atomico
        // do prompt proprio; jobs externos nunca sao tocados). Sem essa API, o cancelamento
        // de um job em execucao espera o fim do processamento atual (resultado descartado).
        ui.showToast(i18n.t('studio.cancelRequested'));
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    }

    /* ------------------------------------------------------------ resultado */
    function showResolutionNote(gen) {
      const params = gen.params || {};
      if (params.mode === 'i2i') {
        els.dims.textContent = i18n.t('studio.generatedI2i', {
          w: gen.width, h: gen.height,
          d: params.denoise !== undefined ? Number(params.denoise).toFixed(2) : '—',
        });
      } else if (params.requested) {
        els.dims.textContent = i18n.t('studio.generatedNative', { w: gen.width, h: gen.height, requested: params.requested });
      }
    }
    function showRefComparison(gen) {
      // Comparacao resultado x referencia no img2img (lightbox nos dois lados).
      if (!els.refcompare || !els.refimg) return;
      const isI2i = gen && gen.params && gen.params.mode === 'i2i';
      if (!isI2i) { els.refcompare.hidden = true; return; }
      const ref = state.lastRef || ((gen.reference_ids || [])[0] ? { id: gen.reference_ids[0], display_name: i18n.t('common.image') } : null);
      if (!ref) { els.refcompare.hidden = true; return; }
      els.refimg.onload = () => { els.refcompare.hidden = false; };
      els.refimg.onerror = () => { els.refcompare.hidden = true; };
      els.refimg.onclick = () => ui.openLightbox({
        url: api.referenceUrl(ref.id),
        title: i18n.t('studio.refTitleName', { name: ref.display_name || i18n.t('common.image') }),
        meta: ref.width && ref.height ? `${ref.width}×${ref.height}` : i18n.t('studio.refMeta'),
      });
      els.refimg.src = api.referenceUrl(ref.id);
    }
    async function renderResult(gen) {
      const image = (gen.images || []).find((item) => item.kind === 'generated') || (gen.images || [])[0];
      if (!image) { ui.showToast(i18n.t('studio.noOutputImage'), 'error'); return; }
      const url = api.imageUrl(gen.id, image.id);
      state.currentGen = gen;
      showEffectiveSeed(gen);
      showResolutionNote(gen);
      showRefComparison(gen);
      if (mode === 'thumbnail') {
        // A imagem gerada vira fundo do editor (nunca e alterada destrutivamente).
        editor.activate();
        editor.setBackgroundFromGeneration(gen);
        editor.setProjectMeta({ id: null, name: (gen.prompt || '').slice(0, 60) });
        state.projectId = null;
        els.actions.hidden = false;
      } else {
        const img = document.getElementById(`${prefix}-img`);
        img.src = url;
        img.hidden = false;
        els.empty.hidden = true;
        els.actions.hidden = false;
      }
      addVariation(gen, image, url);
      if (window.gallery && !document.getElementById('view-gallery').hidden) window.gallery.refresh(false);
    }
    function addVariation(gen, image, url) {
      state.variations = state.variations.filter((item) => item.genId !== gen.id);
      state.variations.unshift({ genId: gen.id, imageId: image.id, url, seed: gen.seed });
      state.variations = state.variations.slice(0, 8);
      renderVariations();
    }
    function renderVariations() {
      els.variations.textContent = '';
      for (const item of state.variations) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'variation-thumb' + (state.currentGen && state.currentGen.id === item.genId ? ' active' : '');
        btn.title = i18n.t('studio.variationTitle', { seed: item.seed });
        btn.setAttribute('aria-label', i18n.t('studio.variationAria', { seed: item.seed }));
        const img = document.createElement('img');
        img.src = item.url;
        img.alt = i18n.t('studio.variationAlt', { seed: item.seed });
        btn.appendChild(img);
        btn.addEventListener('click', async () => {
          try { const result = await api.get(`/api/generations/${item.genId}`); renderResult(result.generation); } catch (err) { ui.showToast(i18n.error(err), 'error'); }
        });
        els.variations.appendChild(btn);
      }
    }

    /* ------------------------------------------------------------ fundo local + projetos (thumbnail) */
    function wireLocalBackground() {
      const pick = document.getElementById('t-bglocal');
      const file = document.getElementById('t-bgfile');
      if (!pick || !file) return;
      pick.addEventListener('click', () => file.click());
      file.addEventListener('change', async () => {
        const selected = file.files[0];
        file.value = '';
        if (!selected) return;
        try {
          // Reaproveita o pipeline de uploads (magic bytes + sandbox isolado + strip de metadados).
          const result = await api.uploadFile('/api/references', selected);
          editor.setBackgroundFromReference(result.reference);
        } catch (err) { ui.showToast(i18n.error(err), 'error'); }
      });
    }
    function wireProjects() {
      document.getElementById('t-projectsrefresh').addEventListener('click', refreshProjects);
      document.getElementById('t-newproject').addEventListener('click', async () => {
        if (!(await confirmDiscard())) return;
        state.projectId = null;
        state.loadedParams = null;
        editor.reset();
        ui.showToast(i18n.t('studio.newBlankProject'));
      });
    }

    /* Etapa 6: alteracoes nao salvas — confirmacao antes de abandonar o projeto editado. */
    async function confirmDiscard() {
      if (!editor || !editor.isDirty()) return true;
      return ui.confirmDialog({
        title: i18n.t('studio.unsavedTitle'),
        message: i18n.t('studio.unsavedMessage'),
        okLabel: i18n.t('studio.unsavedOk'),
      });
    }
    async function refreshProjects() {
      const list = document.getElementById('t-projectlist');
      if (!list) return;
      try {
        const result = await api.get('/api/projects?limit=24');
        list.textContent = '';
        if (!result.projects.length) {
          const p = document.createElement('p');
          p.className = 'hint';
          p.textContent = i18n.t('studio.noProjectsYet');
          list.appendChild(p);
          return;
        }
        for (const project of result.projects) {
          list.appendChild(projectCard(project));
        }
      } catch (err) {
        list.textContent = '';
        const p = document.createElement('p');
        p.className = 'hint';
        p.textContent = i18n.t('studio.projectsListFail', { msg: i18n.error(err) });
        list.appendChild(p);
      }
    }
    function projectCard(project) {
      const card = document.createElement('div');
      card.className = 'proj-card';
      const preview = (project.images || []).find((item) => item.kind === 'preview');
      if (preview) {
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = i18n.t('gallery.previewAlt', { name: project.name });
        img.src = `/api/projects/${project.id}/images/${preview.id}/content`;
        img.addEventListener('click', () => ui.openLightbox({
          url: img.src, title: project.name,
          meta: `${project.width}×${project.height} · ${ui.fmtDate(project.updated_at)}`,
        }));
        card.appendChild(img);
      } else {
        const ph = document.createElement('div');
        ph.className = 'proj-noimg';
        ph.textContent = i18n.t('gallery.noPreviewSmall');
        card.appendChild(ph);
      }
      const body = document.createElement('div');
      body.className = 'proj-body';
      const name = document.createElement('span');
      name.className = 'proj-name';
      name.textContent = project.name;
      name.title = project.name;
      const meta = document.createElement('span');
      meta.className = 'proj-meta mono';
      meta.textContent = `${project.width}×${project.height} · ${ui.fmtDate(project.updated_at)}`;
      body.append(name, meta);
      card.appendChild(body);
      const actions = document.createElement('div');
      actions.className = 'proj-actions';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn xs primary';
      open.textContent = i18n.t('common.open');
      open.addEventListener('click', () => openProject(project));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn xs danger';
      del.textContent = i18n.t('common.delete');
      del.addEventListener('click', () => deleteProject(project));
      actions.append(open, del);
      card.appendChild(actions);
      return card;
    }
    async function openProject(project) {
      if (!(await confirmDiscard())) return;
      try {
        const result = await api.get(`/api/projects/${project.id}`);
        applyLoadedProject(result.project);
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    }

    /* Etapa 6: reabertura a partir da Galeria (projetos editáveis). */
    async function openProjectById(projectId) {
      if (!(await confirmDiscard())) return;
      try {
        const result = await api.get(`/api/projects/${projectId}`);
        applyLoadedProject(result.project);
        window.app.show('thumbnail');
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    }

    function applyLoadedProject(full) {
      if (full.preset && data.platform_presets.some((preset) => preset.key === full.preset)) {
        els.preset.value = full.preset;
      }
      updateDims();
      state.projectId = full.id;
      state.loadedParams = full.params || null;
      state.currentGen = null;
      editor.loadProject(full);
      els.actions.hidden = false; // exportar precisa estar disponivel no projeto reaberto
      ui.showToast(i18n.t('studio.projectOpened', { name: full.name }), 'ok');
    }
    async function deleteProject(project) {
      const ok = await ui.confirmDialog({
        title: i18n.t('studio.deleteProjectTitle'),
        message: i18n.t('studio.deleteProjectMessage', { name: project.name }),
        okLabel: i18n.t('studio.deleteProjectOk'),
      });
      if (!ok) return;
      try {
        await api.del(`/api/projects/${project.id}`);
        if (state.projectId === project.id) { state.projectId = null; state.loadedParams = null; editor.reset(); }
        ui.showToast(i18n.t('studio.projectDeleted'), 'ok');
        refreshProjects();
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    }
    function paramsSnapshot() {
      if (state.currentGen) {
        const gen = state.currentGen;
        return {
          generation_id: gen.id,
          prompt: gen.prompt,
          seed: gen.seed,
          seed_mode: gen.params && gen.params.seed_mode,
          provider: gen.provider,
          style: gen.style,
          preset: gen.preset,
          workflow: gen.params && gen.params.workflow,
          model: gen.params && gen.params.model,
        };
      }
      return state.loadedParams || {};
    }
    async function saveProject() {
      if (!editor) return;
      if (!editor.hasBackground()) {
        ui.showToast(i18n.t('studio.saveNeedsBackground'), 'error');
        return;
      }
      const payload = editor.toProject();
      payload.params = paramsSnapshot();
      payload.preview = editor.previewDataURL();
      try {
        const result = state.projectId
          ? await api.put(`/api/projects/${state.projectId}`, payload)
          : await api.post('/api/projects', payload);
        state.projectId = result.project.id;
        editor.setProjectMeta({ id: state.projectId, name: result.project.name });
        editor.markClean(); // baseline de "alteracoes nao salvas" volta ao zero
        ui.showToast(i18n.t('studio.projectSaved'), 'ok');
        refreshProjects();
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    }

    /* ------------------------------------------------------------ exportacao */
    async function exportComposite({ format, quality, width, height }) {
      if (!editor || !editor.hasBackground()) throw new Error(i18n.t('studio.exportNeedsBackground'));
      if (!state.projectId) await saveProject(); // o estado editavel acompanha a exportacao
      if (!state.projectId) throw new Error(i18n.t('studio.exportSavedBackground'));
      // WYSIWYG: o editor renderiza a mesma cena do preview no tamanho final (PNG sem perdas);
      // o servidor valida, converte (com qualidade JPEG controlada) e registra no projeto.
      const blob = await editor.exportBlob({ width, height });
      const query = new URLSearchParams({
        format, quality: String(quality), width: String(width), height: String(height),
      });
      const result = await api.post(`/api/projects/${state.projectId}/export?${query}`, blob);
      const slug = (editor.toProject().name || 'thumbnail').toLowerCase().replace(/[^\w-]+/g, '-').replace(/^-+|-+$/g, '') || 'thumbnail';
      triggerDownload(
        `/api/projects/${state.projectId}/images/${result.image.id}/content`,
        `${slug}-${width}x${height}.${result.image.ext}`,
      );
      ui.showToast(i18n.t('studio.exportDownloaded'), 'ok');
      if (window.gallery) window.gallery.refresh(false);
    }
    async function exportResize({ format, quality, width, height }) {
      const gen = state.currentGen;
      if (!gen) throw new Error(i18n.t('studio.nothingToResize'));
      const generated = (gen.images || []).find((item) => item.kind === 'generated');
      if (!generated) throw new Error(i18n.t('studio.originalNotFound'));
      const body = { image_id: generated.id, width, height, format };
      if (format === 'jpeg') body.quality = quality;
      const result = await api.post(`/api/generations/${gen.id}/export`, body);
      triggerDownload(api.imageUrl(gen.id, result.image.id), `image-${gen.id.slice(0, 8)}.${format === 'jpeg' ? 'jpg' : 'png'}`);
      ui.showToast(i18n.t('gallery.exportRegistered'), 'ok');
      if (window.gallery) window.gallery.refresh(false);
    }

    /* ------------------------------------------------------------ reabrir da galeria */
    async function reopen(gen) {
      if (mode === 'thumbnail' && !(await confirmDiscard())) return;
      els.prompt.value = gen.prompt || '';
      if (els.langhint) els.langhint.hidden = !/[\u00C0-\u024F\u1E00-\u1EFF]/.test(gen.prompt || '');
      const styleKey = gen.style || 'none';
      els.style.value = [...els.style.options].some((option) => option.value === styleKey) ? styleKey : 'none';
      if (mode === 'thumbnail') {
        if (gen.preset && data.platform_presets.some((preset) => preset.key === gen.preset)) els.preset.value = gen.preset;
      } else {
        const params = gen.params || {};
        if (params.ratio && [...els.preset.options].some((option) => option.value === params.ratio)) els.preset.value = params.ratio;
        const tierSelect = document.getElementById('i-tier');
        if (params.tier && [...tierSelect.options].some((option) => option.value === params.tier)) {
          tierSelect.value = params.tier;
          document.getElementById('i-customdims').hidden = params.tier !== 'custom';
        }
        if (params.tier === 'custom') {
          document.getElementById('i-width').value = gen.width;
          document.getElementById('i-height').value = gen.height;
        }
      }
      const params = gen.params || {};
      setGenMode(params.mode === 'i2i' ? 'i2i' : 't2i');
      if (params.denoise !== undefined && params.denoise !== null) {
        els.denoise.value = String(params.denoise);
        els.denoiseVal.textContent = Number(params.denoise).toFixed(2);
      }
      if (params.quality && [...els.quality.options].some((option) => option.value === params.quality)) els.quality.value = params.quality;
      const hasAdvanced = params.steps !== undefined || params.cfg !== undefined || params.sampler !== undefined
        || params.negative_prompt !== undefined;
      if (params.steps !== undefined) els.steps.value = String(params.steps);
      if (params.cfg !== undefined) els.cfg.value = String(params.cfg);
      if (params.sampler && [...els.sampler.options].some((option) => option.value === params.sampler)) els.sampler.value = params.sampler;
      if (els.negative) els.negative.value = params.negative_prompt !== undefined ? params.negative_prompt : '';
      els.advanced.open = hasAdvanced;
      setSeedMode((gen.params && gen.params.seed_mode) === 'manual' ? 'manual' : 'auto');
      if (gen.seed !== null && gen.seed !== undefined) els.seed.value = String(gen.seed);
      updateDims();
      renderResult(gen);
    }

    if (mode === 'thumbnail') {
      // Aviso nativo do navegador ao fechar/recarregar com alteracoes nao salvas.
      window.addEventListener('beforeunload', (event) => {
        if (editor && editor.isDirty()) { event.preventDefault(); event.returnValue = ''; }
      });
    }

    /* Etapa 10: troca de idioma sem refetch — re-texto do que vive em memoria. */
    function retranslate() {
      if (state.providerInfo) els.note.textContent = providerNoteText(state.providerInfo);
      else els.note.textContent = i18n.t('provider.offlineNote');
      updateDims();
      if (state.currentGen) showEffectiveSeed(state.currentGen);
      renderVariations();
      renderRefs();
      updateExportNote();
    }
    i18n.onChange(retranslate);

    return { init, mode, reopen, openProjectById, refreshProviderNote: loadProviderNote };
  }

  window.createStudio = createStudio;
})();
