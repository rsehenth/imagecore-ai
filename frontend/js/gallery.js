/* Galeria: biblioteca visual de geracoes, projetos editaveis e exportacoes.
   Navegacao (lightbox com setas), busca local, filtros do servidor, detalhe, reexportar,
   reabrir no estudio e excluir com confirmacao. Exportacoes de projeto nunca viram "geracao IA". */
(() => {
  const grid = document.getElementById('g-grid');
  const emptyEl = document.getElementById('g-empty');
  const emptyTitle = document.getElementById('g-empty-title');
  const emptyText = document.getElementById('g-empty-text');
  const emptyCta = document.getElementById('g-empty-cta');
  const moreBtn = document.getElementById('g-more');
  const genFilters = document.getElementById('g-genfilters');
  const countEl = document.getElementById('g-count');
  const searchEl = document.getElementById('g-search');
  const selectModeBtn = document.getElementById('g-select-mode');
  const selectionBar = document.getElementById('g-selection');
  const selectionStatus = document.getElementById('g-selection-status');
  const selectAllBtn = document.getElementById('g-select-all');
  const clearSelectionBtn = document.getElementById('g-clear-selection');
  const deleteSelectionBtn = document.getElementById('g-delete-selection');
  const TABS = { generations: 'g-tab-gens', projects: 'g-tab-projects', exports: 'g-tab-exports' };
  const EMPTY = {
    generations: { title: 'gallery.emptyTitle', text: 'gallery.emptyText', cta: ['image', 'gallery.emptyCta'] },
    projects: { title: 'gallery.emptyProjectsTitle', text: 'gallery.emptyProjectsText', cta: ['thumbnail', 'gallery.emptyProjectsCta'] },
    exports: { title: 'gallery.emptyExportsTitle', text: 'gallery.emptyExportsText', cta: ['image', 'gallery.emptyCta'] },
  };
  const state = {
    items: [], limit: 24, pages: 1, module: '', status: '', request: 0, loading: false,
    timer: null, tab: 'generations', query: '', sig: '', selecting: false, deleting: false,
    selected: new Set(), selectable: [],
  };
  const icon = (name) => ui.icon(name);

  function fetchPage(limit, offset) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (state.module) params.set('module', state.module);
    if (state.status) params.set('status', state.status);
    return api.get(`/api/generations?${params}`);
  }

  /* refresh(false) mantem a posicao; o poll so redesenha quando algo mudou (preserva foco/hover). */
  async function refresh(force = true) {
    if (state.deleting || (!force && state.loading)) return;
    const request = ++state.request;
    state.loading = true;
    let items;
    try {
      if (state.tab === 'projects') {
        const data = await api.get('/api/projects?limit=100');
        items = data.projects;
      } else if (state.tab === 'exports') {
        const [gens, projects] = await Promise.all([api.get('/api/generations?limit=100'), api.get('/api/projects?limit=100')]);
        items = collectExports(gens.generations, projects.projects);
      } else {
        const depth = state.pages * state.limit;
        items = [];
        while (request === state.request && items.length < depth) {
          const limit = Math.min(200, depth - items.length);
          const { generations } = await fetchPage(limit, items.length);
          items.push(...generations);
          if (generations.length < limit) break;
        }
      }
      if (request === state.request) {
        state.items = items;
        paint(force);
      }
    } catch (err) {
      if (request === state.request) ui.showToast(i18n.error(err), 'error');
    } finally {
      if (request === state.request) state.loading = false;
    }
  }
  async function loadMore() {
    if (state.loading || state.deleting) return;
    const request = ++state.request;
    state.loading = true;
    try {
      const data = await fetchPage(state.limit, state.items.length);
      if (request === state.request) {
        state.pages += 1;
        state.items.push(...data.generations);
        paint(true);
      }
    } catch (err) {
      if (request === state.request) ui.showToast(i18n.error(err), 'error');
    } finally {
      if (request === state.request) state.loading = false;
    }
  }
  function paint(force) {
    const sig = `${state.tab}|${state.query}|` + state.items.map((it) => `${it.id}:${it.status || ''}:${it.progress || 0}:${it.updated_at || ''}:${(it.images || []).length}`).join(',');
    if (!force && sig === state.sig) { scheduleAutoRefresh(); return; }
    state.sig = sig;
    render();
  }

  function setTab(tab) {
    if (state.deleting || tab === state.tab) return;
    state.tab = tab;
    state.selecting = false;
    for (const [key, id] of Object.entries(TABS)) {
      const btn = document.getElementById(id);
      btn.classList.toggle('active', key === tab);
      btn.setAttribute('aria-selected', String(key === tab));
    }
    genFilters.hidden = tab !== 'generations'; // filtros so valem para geracoes
    resetFilters();
  }
  for (const [tab, id] of Object.entries(TABS)) document.getElementById(id).addEventListener('click', () => setTab(tab));

  function scheduleAutoRefresh() {
    const active = state.tab === 'generations' && state.items.some((gen) => gen.status === 'running' || gen.status === 'queued');
    if (active && !state.timer) state.timer = setInterval(() => refresh(false), 2500);
    if (!active && state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  function resetFilters() {
    state.items = [];
    state.pages = 1;
    state.sig = '';
    state.selected.clear();
    render();
    refresh();
  }

  function paintSelection() {
    const available = new Set(state.selectable);
    for (const id of state.selected) {
      if (!available.has(id)) state.selected.delete(id);
    }
    const count = state.selected.size;
    selectModeBtn.hidden = state.tab === 'projects' || (!available.size && !state.selecting);
    selectModeBtn.disabled = state.deleting;
    selectModeBtn.setAttribute('aria-pressed', String(state.selecting));
    selectionBar.hidden = !state.selecting;
    selectionStatus.textContent = state.deleting
      ? i18n.t('gallery.deletingSelection')
      : i18n.tn('gallery.selectedCount', count);
    searchEl.disabled = moreBtn.disabled = document.getElementById('g-refresh').disabled = state.deleting;
    for (const control of document.querySelectorAll('#g-genfilters select, .gal-toolbar [role="tab"]')) {
      control.disabled = state.deleting;
    }
    selectAllBtn.disabled = state.deleting || count === available.size;
    clearSelectionBtn.disabled = deleteSelectionBtn.disabled = state.deleting || !count;
    for (const input of grid.querySelectorAll('[data-gallery-pick]')) {
      input.checked = state.selected.has(input.dataset.galleryPick);
      input.disabled = state.deleting;
    }
  }

  function addSelectionControl(card, id, title) {
    const label = el('label', 'gal-pick');
    const input = el('input');
    input.type = 'checkbox';
    input.dataset.galleryPick = id;
    input.setAttribute('aria-label', i18n.t('gallery.selectItem', { title }));
    input.addEventListener('change', () => {
      if (input.checked) state.selected.add(id);
      else state.selected.delete(id);
      paintSelection();
    });
    label.append(input, el('span', '', i18n.t('gallery.selectMode')));
    card.appendChild(label);
  }

  async function deleteSelected() {
    if (state.deleting || !state.selected.size) return;
    const ids = [...state.selected];
    const kind = state.tab;
    const ok = await ui.confirmDialog({
      title: i18n.t('gallery.deleteSelection'),
      message: i18n.tn(kind === 'exports' ? 'gallery.confirmExports' : 'gallery.confirmGenerations', ids.length),
      okLabel: i18n.t('gallery.deleteSelection'),
    });
    if (!ok) return;
    state.deleting = true;
    ++state.request;
    state.loading = false;
    paintSelection();
    try {
      const result = await api.post('/api/gallery/bulk-delete', { kind, ids });
      const removed = new Set(ids);
      state.items = state.items.filter((item) => !removed.has(item.id));
      state.selecting = false;
      state.selected.clear();
      ui.showToast(result.pending_files ? i18n.t('gallery.cleanupPending') : i18n.tn('gallery.selectionDeleted', result.deleted),
        result.pending_files ? 'error' : 'ok');
    } catch (err) {
      ui.showToast(err.code ? i18n.error(err) : i18n.t('error.galleryDeleteFailed'), 'error');
    } finally {
      state.deleting = false;
      render();
      const target = state.selecting ? deleteSelectionBtn : selectModeBtn;
      (target.hidden ? document.getElementById('g-refresh') : target).focus();
      refresh();
    }
  }

  /* ------------------------------------------------------------------ helpers de interface */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function iconBtn(name, label, handler, extra = '') {
    const btn = el('button', `icon-btn ${extra}`.trim());
    btn.type = 'button';
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.innerHTML = icon(name);
    btn.addEventListener('click', (event) => { event.stopPropagation(); handler(); });
    return btn;
  }
  function badge(className, text) { return el('span', className, text); }
  function statusBadge(gen) { return badge(`status-badge ${ui.statusClass(gen.status)}`, ui.statusLabel(gen.status)); }
  function placeholder(text, w, h) {
    const ph = el('div', 'ph', text);
    if (w && h) ph.style.aspectRatio = `${w} / ${h}`;
    return ph;
  }
  function matches(text, extra) {
    const q = state.query.trim().toLowerCase();
    if (!q) return true;
    return `${text || ''} ${extra || ''}`.toLowerCase().includes(q);
  }
  function setCount(shown, total) {
    countEl.textContent = total ? (shown === total ? i18n.tn('gallery.items', total) : i18n.t('gallery.shownOf', { shown: i18n.fmtNum(shown), total: i18n.fmtNum(total) })) : '';
  }
  function showEmpty(filtered) {
    const base = EMPTY[state.tab];
    emptyEl.hidden = false;
    if (filtered) {
      emptyTitle.textContent = i18n.t('gallery.emptyFilteredTitle');
      emptyText.textContent = i18n.t('gallery.emptyFilteredText');
      emptyCta.hidden = true;
    } else {
      emptyTitle.textContent = i18n.t(base.title);
      emptyText.textContent = i18n.t(base.text);
      emptyCta.hidden = false;
      emptyCta.dataset.goto = base.cta[0];
      emptyCta.lastChild.textContent = i18n.t(base.cta[1]);
    }
  }
  /* Conjunto da biblioteca para as setas do lightbox. */
  function lightboxEntries(entries, current) {
    const set = entries.filter((entry) => entry.url);
    return { set, index: Math.max(0, set.findIndex((entry) => entry.url === current)) };
  }
  function openFromSet(entries, entry) {
    const { set, index } = lightboxEntries(entries, entry.url);
    ui.openLightbox({ ...entry, set, index });
  }

  /* ------------------------------------------------------------------ geracoes */
  function genEntry(gen, generated) {
    return {
      url: api.imageUrl(gen.id, generated.id),
      title: gen.prompt.slice(0, 80),
      meta: `${gen.width}×${gen.height} · seed ${gen.seed} · ${ui.fmtBytes(generated.size_bytes)}`,
      filename: `${gen.module}-${gen.id.slice(0, 8)}.${generated.ext}`,
    };
  }
  const primaryImage = (gen) => (gen.images || []).find((item) => item.kind === 'generated') || (gen.images || [])[0];
  const selectableGeneration = (gen) => !['queued', 'running'].includes(gen.status);

  function render() {
    const focused = document.activeElement?.dataset.galleryPick;
    grid.textContent = '';
    state.selectable = [];
    moreBtn.hidden = true;
    emptyEl.hidden = true;
    if (state.tab === 'projects') renderProjects();
    else if (state.tab === 'exports') renderExports();
    else renderGenerations();
    paintSelection();
    if (focused) {
      const input = [...grid.querySelectorAll('[data-gallery-pick]')].find((item) => item.dataset.galleryPick === focused);
      (input || selectModeBtn).focus({ preventScroll: true });
    }
    scheduleAutoRefresh();
  }

  function renderGenerations() {
    const visible = state.items.filter((gen) => matches(gen.prompt, `${gen.seed} ${gen.provider} ${gen.module}`));
    state.selectable = visible.filter(selectableGeneration).map((gen) => gen.id);
    setCount(visible.length, state.items.length);
    if (!visible.length) showEmpty(state.items.length > 0);
    const entries = visible.map((gen) => { const img = primaryImage(gen); return img ? genEntry(gen, img) : null; }).filter(Boolean);
    for (const gen of visible) grid.appendChild(genCard(gen, entries));
    moreBtn.hidden = !(state.items.length === state.pages * state.limit && !state.query);
  }

  function genCard(gen, entries) {
    const card = el('article', 'gal-card');
    if (state.selecting && selectableGeneration(gen)) addSelectionControl(card, gen.id, gen.prompt.slice(0, 80));
    const thumb = el('div', 'thumb');
    thumb.append(badge('kind-badge', gen.module === 'thumbnail' ? i18n.t('gallery.kindThumbnail') : i18n.t('gallery.kindImage')), statusBadge(gen));
    const generated = primaryImage(gen);
    if (generated) {
      const entry = genEntry(gen, generated);
      const img = el('img');
      img.loading = 'lazy';
      img.alt = gen.prompt.slice(0, 80);
      img.src = entry.url;
      img.width = gen.width;
      img.height = gen.height;
      img.addEventListener('click', () => openFromSet(entries, entry));
      thumb.appendChild(img);
    } else if (gen.status === 'running' || gen.status === 'queued') {
      thumb.appendChild(placeholder(`${gen.stage ? i18n.stage(gen.stage) : i18n.t('status.queued')}… ${gen.progress}%`, gen.width, gen.height));
    } else {
      thumb.appendChild(placeholder(gen.status === 'failed' ? i18n.t('gallery.genFailed') : i18n.t('gallery.noImage'), gen.width, gen.height));
    }
    card.appendChild(thumb);

    const body = el('div', 'gal-body');
    const prompt = el('p', 'gal-prompt', gen.prompt);
    prompt.title = gen.prompt;
    body.append(prompt, el('span', 'gal-meta', `${gen.width}×${gen.height} · seed ${gen.seed} · ${gen.provider} · ${ui.fmtDate(gen.created_at)}`));
    if (gen.status === 'failed' && gen.error) body.appendChild(el('span', 'gal-fail', gen.error));
    card.appendChild(body);

    const actions = el('div', 'gal-actions');
    actions.append(
      iconBtn('info', i18n.t('gallery.viewDetails'), () => openDetail(gen)),
      iconBtn('open', i18n.t('gallery.reopenStudio'), () => reopen(gen)),
      iconBtn('trash', i18n.t('gallery.deleteGen'), () => remove(gen), 'danger'),
    );
    if (!state.selecting) card.appendChild(actions);
    return card;
  }

  /* ------------------------------------------------------------------ projetos editaveis */
  const previewUrl = (project) => {
    const preview = (project.images || []).find((item) => item.kind === 'preview');
    return preview ? `/api/projects/${project.id}/images/${preview.id}/content` : null;
  };
  function renderProjects() {
    const visible = state.items.filter((project) => matches(project.name, project.preset));
    setCount(visible.length, state.items.length);
    if (!visible.length) showEmpty(state.items.length > 0);
    const entries = visible.map((project) => (previewUrl(project) ? projectEntry(project) : null)).filter(Boolean);
    for (const project of visible) grid.appendChild(projectCard(project, entries));
  }
  const projectEntry = (project) => ({
    url: previewUrl(project), title: project.name, meta: `${project.width}×${project.height} · ${ui.fmtDate(project.updated_at)}`,
  });

  function projectCard(project, entries) {
    const card = el('article', 'gal-card');
    const thumb = el('div', 'thumb');
    thumb.appendChild(badge('kind-badge', i18n.t('gallery.kindProject')));
    const url = previewUrl(project);
    if (url) {
      const img = el('img');
      img.loading = 'lazy';
      img.alt = i18n.t('gallery.previewAlt', { name: project.name });
      img.src = url;
      img.width = project.width;
      img.height = project.height;
      img.addEventListener('click', () => openFromSet(entries, projectEntry(project)));
      thumb.appendChild(img);
    } else {
      thumb.appendChild(placeholder(i18n.t('gallery.noPreview'), project.width, project.height));
    }
    card.appendChild(thumb);

    const body = el('div', 'gal-body');
    const name = el('p', 'gal-prompt', project.name);
    name.title = project.name;
    const exports = (project.images || []).filter((item) => item.kind === 'export');
    const exportCount = exports.length ? `${i18n.tn('gallery.exportsCount', exports.length)} · ` : '';
    body.append(name, el('span', 'gal-meta', `${project.width}×${project.height} · ${exportCount}${i18n.t('gallery.updated', { date: ui.fmtDate(project.updated_at) })}`));
    card.appendChild(body);

    const actions = el('div', 'gal-actions');
    actions.append(
      iconBtn('layers', i18n.t('gallery.openInStudio'), () => openInStudio(project)),
      iconBtn('info', i18n.t('gallery.viewDetails'), () => openProjectDetail(project.id)),
      iconBtn('trash', i18n.t('gallery.deleteProject'), () => removeProject(project), 'danger'),
    );
    card.appendChild(actions);
    return card;
  }

  /* ------------------------------------------------------------------ exportacoes */
  function collectExports(generations, projects) {
    const out = [];
    for (const gen of generations || []) {
      for (const item of (gen.images || []).filter((image) => image.kind === 'export')) {
        out.push({
          id: item.id, url: api.imageUrl(gen.id, item.id), width: item.width, height: item.height, ext: item.ext,
          galleryExport: true,
          created_at: item.created_at, size_bytes: item.size_bytes,
          origin: gen.module === 'thumbnail' ? i18n.t('gallery.kindThumbnail') : i18n.t('gallery.kindImage'), title: gen.prompt,
          filename: `${gen.module}-${item.id.slice(0, 8)}.${item.ext}`,
        });
      }
    }
    for (const project of projects || []) {
      for (const item of (project.images || []).filter((image) => image.kind === 'export')) {
        out.push({
          id: item.id, url: `/api/projects/${project.id}/images/${item.id}/content`, width: item.width, height: item.height, ext: item.ext,
          created_at: item.created_at, size_bytes: item.size_bytes, origin: i18n.t('gallery.kindProject'), title: project.name,
          filename: `projeto-${item.id.slice(0, 8)}.${item.ext}`,
        });
      }
    }
    return out.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  }
  function renderExports() {
    const visible = state.items.filter((item) => matches(item.title, `${item.ext} ${item.origin}`));
    state.selectable = visible.filter((item) => item.galleryExport).map((item) => item.id);
    setCount(visible.length, state.items.length);
    if (!visible.length) showEmpty(state.items.length > 0);
    const entries = visible.map((item) => ({
      url: item.url, title: item.title.slice(0, 80), filename: item.filename,
      meta: `${item.width}×${item.height} · ${item.ext.toUpperCase()} · ${ui.fmtBytes(item.size_bytes)}`,
    }));
    visible.forEach((item, index) => {
      const card = el('article', 'gal-card');
      if (state.selecting && item.galleryExport) addSelectionControl(card, item.id, item.title.slice(0, 80));
      const thumb = el('div', 'thumb');
      thumb.append(badge('kind-badge', `${item.origin} · ${item.ext.toUpperCase()}`));
      const img = el('img');
      img.loading = 'lazy';
      img.alt = i18n.t('gallery.exportAlt', { title: item.title.slice(0, 60) });
      img.src = item.url;
      if (item.width && item.height) { img.width = item.width; img.height = item.height; }
      img.addEventListener('click', () => openFromSet(entries, entries[index]));
      thumb.appendChild(img);
      card.appendChild(thumb);
      const body = el('div', 'gal-body');
      const title = el('p', 'gal-prompt', item.title);
      title.title = item.title;
      body.append(title, el('span', 'gal-meta', `${item.width}×${item.height} · ${item.ext.toUpperCase()} · ${ui.fmtBytes(item.size_bytes)} · ${ui.fmtDate(item.created_at)}`));
      card.appendChild(body);
      const actions = el('div', 'gal-actions');
      const download = el('a', 'icon-btn');
      download.href = item.url;
      download.setAttribute('download', item.filename);
      download.title = i18n.t('gallery.downloadFile');
      download.setAttribute('aria-label', i18n.t('gallery.downloadExport'));
      download.innerHTML = icon('download');
      actions.appendChild(download);
      card.appendChild(actions);
      grid.appendChild(card);
    });
  }

  /* ------------------------------------------------------------------ acoes */
  function openInStudio(project) {
    const studio = window.studios && window.studios.thumbnail;
    if (!studio) return;
    studio.openProjectById(project.id);
  }

  async function removeProject(project) {
    const ok = await ui.confirmDialog({
      title: i18n.t('gallery.deleteProjectTitle'),
      message: i18n.t('gallery.deleteProjectMessage', { name: project.name }),
      okLabel: i18n.t('gallery.deleteProjectOk'),
    });
    if (!ok) return;
    try {
      await api.del(`/api/projects/${project.id}`);
      ui.showToast(i18n.t('gallery.projectDeleted'), 'ok');
      refresh();
    } catch (err) { ui.showToast(i18n.error(err), 'error'); }
  }

  async function remove(gen) {
    const ok = await ui.confirmDialog({
      title: i18n.t('gallery.deleteGenTitle'),
      message: i18n.t('gallery.deleteGenMessage', {
        kind: gen.module === 'thumbnail' ? i18n.t('gallery.kindThumbnailName') : i18n.t('gallery.kindImageName'),
        prompt: gen.prompt.slice(0, 120),
      }),
      okLabel: i18n.t('gallery.deleteGenOk'),
    });
    if (!ok) return;
    try {
      const result = await api.del(`/api/generations/${gen.id}`);
      ui.showToast(i18n.t(result.pending_files ? 'gallery.cleanupPending' : 'gallery.genDeleted'),
        result.pending_files ? 'error' : 'ok');
      refresh();
    } catch (err) { ui.showToast(i18n.error(err), 'error'); }
  }

  async function reopen(gen) {
    const studio = window.studios && window.studios[gen.module];
    if (!studio) return;
    window.app.show(gen.module);
    studio.reopen(gen);
  }

  function row(label, value) {
    const div = el('div', 'row');
    div.append(el('span', 'k', label), el('span', 'v', value));
    return div;
  }
  function exportLink(href, filename, item) {
    const link = el('a', 'btn xs');
    link.href = href;
    link.setAttribute('download', filename);
    link.innerHTML = icon('download');
    link.append(`${item.width}×${item.height} · ${item.ext.toUpperCase()} · ${ui.fmtDate(item.created_at)}`);
    return link;
  }

  /* ------------------------------------------------------------------ detalhe (projeto) */
  function makeModal(title, ids, footerHtml) {
    const modal = el('div', 'modal hidden');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', title);
    const card = el('div', 'modal-card image-lightbox');
    card.innerHTML = `
      <header class="modal-head"><h2></h2>
        <button class="icon-btn" type="button" data-close>${icon('x')}</button>
      </header>
      <div class="modal-body">
        <div class="detail-grid">
          <div class="detail-media"><img id="${ids}-img" alt=""></div>
          <div class="detail-info">
            <div id="${ids}-rows" class="set-body"></div>
            <div id="${ids}-exports" class="detail-exports"></div>
          </div>
        </div>
      </div>
      <footer class="modal-actions">${footerHtml}</footer>`;
    card.querySelector('h2').textContent = title;
    card.querySelector('[data-close]').setAttribute('aria-label', i18n.t('common.close'));
    modal.appendChild(card);
    document.body.appendChild(modal);
    return modal;
  }

  let projectModal = null;
  function ensureProjectModal() {
    if (projectModal) return projectModal;
    projectModal = makeModal(i18n.t('gallery.detailProjectTitle'), 'pproj', `
      <button class="btn danger spacer" type="button" id="pproj-delete">${icon('trash')}${i18n.t('gallery.detailDelete')}</button>
      <button class="btn primary" type="button" id="pproj-studio">${icon('layers')}${i18n.t('gallery.openInStudio')}</button>`);
    return projectModal;
  }

  async function openProjectDetail(projectId) {
    let full;
    try { full = (await api.get(`/api/projects/${projectId}`)).project; } catch (err) { ui.showToast(i18n.error(err), 'error'); return; }
    const modal = ensureProjectModal();
    const img = modal.querySelector('#pproj-img');
    const rows = modal.querySelector('#pproj-rows');
    const exportsBox = modal.querySelector('#pproj-exports');
    const preview = (full.images || []).find((item) => item.kind === 'preview');
    img.hidden = !preview;
    if (preview) {
      img.src = `/api/projects/${full.id}/images/${preview.id}/content`;
      img.onclick = () => ui.openLightbox({ url: img.src, title: full.name, meta: `${full.width}×${full.height} · ${ui.fmtDate(full.updated_at)}` });
    }
    rows.textContent = '';
    const layers = Array.isArray(full.layers) ? full.layers.length : 0;
    rows.append(
      row(i18n.t('gallery.rowName'), full.name),
      row(i18n.t('gallery.rowPreset'), full.preset || '—'),
      row(i18n.t('gallery.rowDims'), `${full.width}×${full.height}`),
      row(i18n.t('gallery.rowTextLayers'), String(layers)),
      row(i18n.t('gallery.rowBackground'), backgroundLabel(full)),
      row(i18n.t('gallery.rowCreatedAt'), ui.fmtDate(full.created_at)),
      row(i18n.t('gallery.rowUpdatedAt'), ui.fmtDate(full.updated_at)),
    );
    exportsBox.textContent = '';
    exportsBox.appendChild(el('h3', '', i18n.t('gallery.projectExports')));
    const exportImages = (full.images || []).filter((item) => item.kind === 'export');
    if (!exportImages.length) exportsBox.appendChild(el('p', 'hint', i18n.t('gallery.noExportsYet')));
    for (const item of exportImages) {
      exportsBox.appendChild(exportLink(`/api/projects/${full.id}/images/${item.id}/content`, `projeto-${item.id.slice(0, 8)}.${item.ext}`, item));
    }
    modal.querySelector('#pproj-delete').onclick = async () => { ui.closeModal(modal); await removeProject(full); };
    modal.querySelector('#pproj-studio').onclick = () => { ui.closeModal(modal); openInStudio(full); };
    ui.openModal(modal);
  }

  function backgroundLabel(project) {
    const bg = project.background || {};
    const source = bg.source === 'generation' ? i18n.t('gallery.bgGeneration')
      : bg.source === 'reference' ? i18n.t('gallery.bgReference') : i18n.t('gallery.bgNone');
    const origin = bg.copy_image_id ? i18n.t('gallery.bgOwnCopy') : i18n.t('gallery.bgExternal');
    if (bg.source === 'generation' || bg.source === 'reference') return `${source} (${origin})`;
    return source;
  }

  /* ------------------------------------------------------------------ detalhe (geracao) */
  let detailModal = null;
  function ensureDetailModal() {
    if (detailModal) return detailModal;
    detailModal = makeModal(i18n.t('gallery.detailGenTitle'), 'detail', `
      <button class="btn danger spacer" type="button" id="detail-delete">${icon('trash')}${i18n.t('gallery.detailDelete')}</button>
      <button class="btn" type="button" id="detail-studio">${icon('open')}${i18n.t('gallery.detailReopen')}</button>
      <button class="btn primary" type="button" id="detail-export">${icon('download')}${i18n.t('common.export')}</button>`);
    return detailModal;
  }

  async function openDetail(gen) {
    let full = gen;
    try { full = (await api.get(`/api/generations/${gen.id}`)).generation; } catch (err) { ui.showToast(i18n.error(err), 'error'); return; }
    const modal = ensureDetailModal();
    const img = modal.querySelector('#detail-img');
    const rows = modal.querySelector('#detail-rows');
    const exportsBox = modal.querySelector('#detail-exports');
    const exportBtn = modal.querySelector('#detail-export');
    const generated = primaryImage(full);

    img.hidden = !generated;
    exportBtn.disabled = !generated;
    if (generated) {
      img.src = api.imageUrl(full.id, generated.id);
      img.onclick = () => ui.openLightbox(genEntry(full, generated));
    }
    rows.textContent = '';
    const seedModeLabel = full.params && full.params.seed_mode === 'manual' ? i18n.t('gallery.seedManual') : i18n.t('gallery.seedAuto');
    const params = full.params || {};
    const resolutionFinal = params.mode === 'i2i'
      ? `${full.width}×${full.height} (${i18n.t('gallery.resLatent')})`
      : params.requested
        ? `${generated ? `${generated.width}×${generated.height}` : `${full.width}×${full.height}`} (${i18n.t('gallery.resNative')}) ${i18n.t('gallery.resExportNote', { requested: params.requested })}`
        : `${full.width}×${full.height}`;
    rows.append(
      row(i18n.t('gallery.rowModule'), full.module === 'thumbnail' ? i18n.t('thumb.title') : i18n.t('creator.title')),
      row(i18n.t('gallery.rowStatus'), `${ui.statusLabel(full.status)}${full.error ? ` — ${full.error}` : ''}`),
      row(i18n.t('gallery.rowPrompt'), full.prompt),
      row(i18n.t('gallery.rowFinalPrompt'), params.final_prompt || full.prompt),
      ...(params.final_negative_prompt !== undefined ? [row(i18n.t('gallery.rowNegative'), params.final_negative_prompt || i18n.t('gallery.emptyNegative'))] : []),
      row(i18n.t('gallery.rowStyle'), params.style_label || full.style),
      row(i18n.t('gallery.rowSeed'), `${full.seed} (${seedModeLabel})`),
      row(i18n.t('gallery.rowResolution'), resolutionFinal),
      row(i18n.t('gallery.rowEngine'), full.provider),
      ...(params.profile ? [row(i18n.t('gallery.rowProfile'), params.profile)] : []),
      ...(params.workflow ? [row(i18n.t('gallery.rowWorkflow'), params.workflow)] : []),
      ...(params.model ? [row(i18n.t('gallery.rowModel'), params.model)] : []),
      row(i18n.t('gallery.rowReferences'), String((full.reference_ids || []).length)),
      row(i18n.t('gallery.rowCreatedAt'), ui.fmtDate(full.created_at)),
    );
    exportsBox.textContent = '';
    const exportImages = (full.images || []).filter((item) => item.kind === 'export');
    if (exportImages.length) {
      exportsBox.appendChild(el('h3', '', i18n.t('gallery.exportsTitle')));
      for (const item of exportImages) exportsBox.appendChild(exportLink(api.imageUrl(full.id, item.id), `${full.module}-${item.id.slice(0, 8)}.${item.ext}`, item));
    }

    modal.querySelector('#detail-delete').onclick = async () => { ui.closeModal(modal); await remove(full); };
    modal.querySelector('#detail-studio').onclick = () => { ui.closeModal(modal); reopen(full); };
    exportBtn.onclick = () => {
      if (!generated) { ui.showToast(i18n.t('gallery.nothingToExport'), 'error'); return; }
      exporter.openExportDialog({
        baseDims: [full.width, full.height],
        allowFormat: true,
        onExport: async ({ format, quality, width, height }) => {
          const body = { image_id: generated.id, width, height, format };
          if (format === 'jpeg') body.quality = quality;
          const result = await api.post(`/api/generations/${full.id}/export`, body);
          const a = document.createElement('a');
          a.href = api.imageUrl(full.id, result.image.id);
          a.download = `${full.module}-${result.image.id.slice(0, 8)}.${format === 'jpeg' ? 'jpg' : 'png'}`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          ui.showToast(i18n.t('gallery.exportRegistered'), 'ok');
          refresh();
        },
      });
    };
    ui.openModal(modal);
  }

  /* ------------------------------------------------------------------ eventos */
  document.getElementById('g-refresh').addEventListener('click', () => refresh());
  document.getElementById('g-module').addEventListener('change', (event) => { state.module = event.target.value; resetFilters(); });
  document.getElementById('g-status').addEventListener('change', (event) => { state.status = event.target.value; resetFilters(); });
  searchEl.addEventListener('input', () => {
    state.query = searchEl.value;
    state.sig = '';
    render();
  });
  moreBtn.addEventListener('click', loadMore);
  selectModeBtn.addEventListener('click', () => {
    state.selecting = !state.selecting;
    state.selected.clear();
    render();
  });
  selectAllBtn.addEventListener('click', () => {
    state.selected = new Set(state.selectable);
    paintSelection();
    clearSelectionBtn.focus();
  });
  clearSelectionBtn.addEventListener('click', () => {
    state.selected.clear();
    paintSelection();
    if (!selectAllBtn.disabled) selectAllBtn.focus();
  });
  deleteSelectionBtn.addEventListener('click', deleteSelected);

  /* Troca de idioma: re-render com o estado em memoria (sem refetch). */
  i18n.onChange(() => render());

  window.gallery = { refresh };
})();
