/* Configuracoes: motor, modelos, padroes de geracao, GPU/memoria e diagnosticos.
   Somente leitura, exceto a liberacao controlada de memoria do motor (com confirmacao). */
(() => {
  const engineBody = document.getElementById('s-provider-body');
  const profilesBody = document.getElementById('s-profiles-body');
  const genBody = document.getElementById('s-gen-body');
  const gpuBody = document.getElementById('s-gpu-body');
  const advBody = document.getElementById('s-adv-body');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function row(label, value) {
    const div = el('div', 'row');
    div.append(el('span', 'k', label), el('span', 'v', value));
    return div;
  }
  function badge(kind, text) { return el('span', `badge ${kind}`, text); }
  function loading(...bodies) {
    for (const body of bodies) { body.textContent = ''; body.appendChild(el('p', 'loading', i18n.t('common.loading'))); }
  }
  function failure(body, text) { body.textContent = ''; body.appendChild(el('p', 'gal-fail', text)); }
  /* status_label vem do backend em pt-BR: o rotulo e traduzido pela CHAVE estavel do status;
     status fora do mapa (futuro) cai no texto do backend. Ja o motivo (status_reason) vem com
     `status_code` estavel (Etapa 10.1) + `missing_files`; sem codigo conhecido, o texto bruto. */
  const STATUS_LABEL_KEYS = {
    instalado: 'profile.instalado',
    compativel: 'profile.compativel',
    nao_validado: 'profile.nao_validado',
    indisponivel: 'profile.indisponivel',
  };
  const STATUS_REASON_KEYS = {
    workflow_missing: 'profile.reasonWorkflowMissing',
    weights_unverified: 'profile.reasonWeightsUnverified',
    weights_missing: 'profile.reasonWeightsMissing',
    smoke_pending: 'profile.reasonSmokePending',
    installed_ok: 'profile.reasonInstalledOk',
  };
  const profileReasonText = (profile) => {
    const key = STATUS_REASON_KEYS[profile.status_code];
    return key ? i18n.t(key, { files: (profile.missing_files || []).join(', ') }) : profile.status_reason;
  };
  const QUALITY_LABEL_KEYS = { rapida: 'quality.rapida', padrao: 'quality.padrao', alta: 'quality.alta' };
  const profileTone = (status) => (status === 'instalado' ? 'ok' : status === 'indisponível' || status === 'indisponivel' ? 'err' : 'warn');
  const profileStatusText = (profile) => (STATUS_LABEL_KEYS[profile.status] ? i18n.t(STATUS_LABEL_KEYS[profile.status]) : profile.status_label);
  const qualityLabelText = (preset) => (QUALITY_LABEL_KEYS[preset.key] ? i18n.t(QUALITY_LABEL_KEYS[preset.key], { steps: preset.steps, cfg: preset.cfg }) : preset.label);

  async function loadProfiles() {
    try {
      const data = await api.get('/api/profiles');
      profilesBody.textContent = '';
      const list = data.profiles || [];
      if (!list.length) profilesBody.appendChild(el('p', 'hint', i18n.t('settings.noProfiles')));
      for (const profile of list) {
        const item = el('div', 'profile');
        const top = el('div', 'profile-top');
        top.append(el('strong', '', profile.label), badge(profileTone(profile.status), profileStatusText(profile)));
        item.append(top, el('p', 'hint', i18n.t('settings.profileHint', {
          reason: profileReasonText(profile), license: profile.license, download: profile.estimated_download_label,
        })));
        profilesBody.appendChild(item);
      }
    } catch (err) { failure(profilesBody, i18n.t('settings.failProfiles', { msg: i18n.error(err) })); }
  }

  async function loadEngine() {
    try {
      const data = await api.get('/api/provider');
      const caps = data.capabilities || {};
      const ok = data.health && data.health.ok;
      engineBody.textContent = '';
      engineBody.append(
        row(i18n.t('settings.engine'), data.name === 'simulated' ? i18n.t('provider.simulatedLong') : data.name),
        (() => { const r = row(i18n.t('settings.state'), ''); r.lastChild.append(badge(ok ? 'ok' : 'err', ok ? i18n.t('settings.ok') : i18n.t('settings.fail'))); if (!ok && data.health && data.health.error) r.lastChild.append(` ${data.health.error}`); return r; })(),
        row(i18n.t('settings.references'), caps.references ? i18n.t('settings.referencesYes', { n: caps.max_references }) : i18n.t('settings.referencesNo')),
      );
      if (caps.workflow) engineBody.appendChild(row(i18n.t('settings.workflow'), caps.workflow));
      if (caps.model) engineBody.appendChild(row(i18n.t('settings.model'), caps.model));
      if (caps.job_cancel_api !== undefined) {
        engineBody.appendChild(row(i18n.t('settings.cancelApi'), caps.job_cancel_api === true ? i18n.t('settings.cancelApiYes') : caps.job_cancel_api === false ? i18n.t('settings.cancelApiNo') : i18n.t('settings.cancelApiDetected')));
      }
      if (caps.note) engineBody.appendChild(el('p', 'hint', caps.note));
      return data;
    } catch (err) { failure(engineBody, i18n.t('settings.failEngine', { msg: i18n.error(err) })); return null; }
  }

  async function loadGenerationDefaults() {
    try {
      const data = await api.get('/api/presets');
      genBody.textContent = '';
      for (const preset of data.quality_presets || []) {
        genBody.appendChild(row(i18n.t('settings.qualityRow', { label: qualityLabelText(preset) }), `${preset.steps} steps · CFG ${preset.cfg}`));
      }
      const ranges = data.ranges || {};
      if (ranges.steps) genBody.appendChild(row(i18n.t('settings.stepsAllowed'), i18n.t('settings.range', { a: ranges.steps[0], b: ranges.steps[1] })));
      if (ranges.cfg) genBody.appendChild(row(i18n.t('settings.cfgAllowed'), i18n.t('settings.range', { a: ranges.cfg[0], b: ranges.cfg[1] })));
      if ((data.samplers || []).length) genBody.appendChild(row(i18n.t('settings.samplers'), data.samplers.join(', ')));
      genBody.appendChild(el('p', 'hint', i18n.t('settings.generationDefaultsHint')));
    } catch (err) { failure(genBody, i18n.t('settings.failDefaults', { msg: i18n.error(err) })); }
  }

  function gpuBlock(gpu) {
    const used = Number(gpu.memory_used_mb) || 0;
    const total = Number(gpu.memory_total_mb) || 0;
    const pct = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
    const block = el('div', 'gpu');
    const top = el('div', 'gpu-top');
    top.append(el('span', '', gpu.name), el('span', 'mono', `${used} / ${total} MB`));
    const meter = el('div', `meter${pct >= 85 ? ' hot' : ''}`);
    meter.setAttribute('role', 'meter');
    meter.setAttribute('aria-label', i18n.t('settings.vramAria', { name: gpu.name }));
    meter.setAttribute('aria-valuemin', '0');
    meter.setAttribute('aria-valuemax', String(total));
    meter.setAttribute('aria-valuenow', String(used));
    meter.appendChild(Object.assign(document.createElement('i'), { style: `width:${pct}%` }));
    const stats = el('div', 'gpu-stats mono');
    for (const text of [
      i18n.t('settings.vramFree', { n: gpu.memory_free_mb }),
      i18n.t('settings.gpuUsage', { pct: gpu.utilization_gpu_percent ?? '—' }),
      i18n.t('settings.temp', { c: gpu.temperature_c ?? '—' }),
    ]) stats.appendChild(el('span', '', text));
    block.append(top, meter, stats);
    return block;
  }

  function freeMemoryButton() {
    const actions = el('div', 'set-actions');
    const btn = el('button', 'btn', i18n.t('settings.freeMemory'));
    btn.type = 'button';
    btn.title = i18n.t('settings.freeMemoryTitle');
    btn.addEventListener('click', async () => {
      // Etapa 8: liberacao CONTROLADA da memoria do motor. Nunca automatica: exige confirmacao,
      // instancia DEDICADA declarada no config e fila do ComfyUI comprovadamente vazia.
      const ok = await ui.confirmDialog({
        title: i18n.t('settings.freeMemoryTitle2'),
        message: i18n.t('settings.freeMemoryMessage'),
        okLabel: i18n.t('settings.freeMemoryOk'),
      });
      if (!ok) return;
      try {
        const result = await api.post('/api/system/engine/free', { confirm: true });
        ui.showToast(result && result.note ? result.note : i18n.t('settings.memoryFreed'), 'ok');
        load();
      } catch (err) { ui.showToast(i18n.error(err), 'error'); }
    });
    actions.append(btn, el('span', 'hint', i18n.t('settings.freeMemoryHint')));
    return actions;
  }

  async function loadDiagnostics(engine) {
    try {
      const diag = await api.get('/api/diagnostics');
      gpuBody.textContent = '';
      const gpus = diag.gpu;
      if (!gpus || !gpus.length) {
        gpuBody.appendChild(el('p', '', i18n.t('settings.noGpu')));
      } else {
        for (const gpu of gpus) gpuBody.appendChild(gpuBlock(gpu));
        gpuBody.appendChild(row(i18n.t('settings.preflight'), diag.gpu_preflight.enabled ? i18n.t('settings.preflightOn', { n: diag.gpu_preflight.min_free_vram_mb }) : i18n.t('settings.preflightOff')));
      }
      if (engine && engine.required_vram_mb !== undefined) gpuBody.appendChild(row(i18n.t('settings.estimatedVram'), i18n.t('settings.estimatedVramValue', { n: engine.required_vram_mb })));
      gpuBody.appendChild(freeMemoryButton());

      advBody.textContent = '';
      advBody.append(
        row(i18n.t('settings.app'), `${diag.app.name} v${diag.app.version}`),
        row(i18n.t('settings.address'), `http://${diag.app.host}:${diag.app.port}`),
        el('p', 'hint', i18n.t('settings.minimaxHint')),
      );
      const actions = el('div', 'set-actions');
      const copy = el('button', 'btn', i18n.t('settings.copyDiag'));
      copy.type = 'button';
      const json = JSON.stringify(diag, null, 2);
      const pre = el('pre', 'diag', json);
      pre.hidden = true;
      const toggle = el('button', 'btn ghost', i18n.t('settings.showJson'));
      toggle.type = 'button';
      toggle.setAttribute('aria-expanded', 'false');
      toggle.addEventListener('click', () => {
        pre.hidden = !pre.hidden;
        toggle.setAttribute('aria-expanded', String(!pre.hidden));
        toggle.textContent = pre.hidden ? i18n.t('settings.showJson') : i18n.t('settings.hideJson');
      });
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(json); ui.showToast(i18n.t('settings.diagCopied'), 'ok'); }
        catch (err) { ui.showToast(i18n.t('settings.diagCopyFail'), 'error'); }
      });
      actions.append(copy, toggle);
      advBody.append(actions, pre);
    } catch (err) {
      failure(gpuBody, i18n.t('settings.failDiag', { msg: i18n.error(err) }));
      failure(advBody, i18n.t('settings.failDiag', { msg: i18n.error(err) }));
    }
  }

  async function load() {
    loading(engineBody, profilesBody, genBody, gpuBody, advBody);
    const [engine] = await Promise.all([loadEngine(), loadProfiles(), loadGenerationDefaults()]);
    await loadDiagnostics(engine);
  }

  document.getElementById('s-refresh').addEventListener('click', load);
  /* Troca de idioma: recarrega as secoes so se a tela estiver visivel (dados sao read-only). */
  i18n.onChange(() => { if (!document.getElementById('view-settings').hidden) load(); });
  window.settingsView = { load };
})();
