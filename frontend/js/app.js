/* Roteamento entre views, preferencias da interface e status do motor. */
(() => {
  const views = ['image', 'thumbnail', 'gallery', 'settings'];
  const root = document.documentElement;

  /* ------------------------------------------------------------ preferencias (localStorage opcional) */
  const store = {
    get(key) { try { return window.localStorage.getItem(`imagecore.${key}`); } catch (err) { return null; } },
    set(key, value) { try { window.localStorage.setItem(`imagecore.${key}`, value); } catch (err) { /* sem persistencia: segue valendo na sessao */ } },
  };
  function applyPrefs() {
    root.dataset.density = store.get('density') === 'compact' ? 'compact' : 'comfortable';
    root.dataset.motion = store.get('motion') === 'reduce' ? 'reduce' : 'auto';
  }
  function wirePrefs() {
    for (const name of ['density', 'motion']) {
      const radios = document.querySelectorAll(`input[name="pref-${name}"]`);
      const saved = store.get(name);
      for (const radio of radios) {
        if (saved) radio.checked = radio.value === saved;
        radio.addEventListener('change', () => { if (radio.checked) { store.set(name, radio.value); applyPrefs(); } });
      }
    }
    const home = document.getElementById('pref-home');
    home.value = store.get('home') || 'last';
    home.addEventListener('change', () => store.set('home', home.value));
  }
  applyPrefs();

  const studios = {
    thumbnail: createStudio({ prefix: 't', mode: 'thumbnail' }),
    image: createStudio({ prefix: 'i', mode: 'image' }),
  };

  function show(name, { focus = true } = {}) {
    if (!views.includes(name)) name = 'image';
    for (const view of views) document.getElementById(`view-${view}`).hidden = view !== name;
    document.querySelectorAll('.nav-item').forEach((btn) => {
      const on = btn.dataset.view === name;
      btn.classList.toggle('active', on);
      if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
    });
    store.set('view', name);
    if (name === 'gallery' && window.gallery) window.gallery.refresh();
    if (name === 'settings' && window.settingsView) window.settingsView.load();
    // A tela nova abre no inicio e o foco acompanha a navegacao.
    const view = document.getElementById(`view-${name}`);
    view.scrollTop = 0;
    if (focus) document.getElementById('main').focus({ preventScroll: true });
  }

  for (const btn of document.querySelectorAll('.nav-item')) {
    btn.addEventListener('click', () => show(btn.dataset.view));
  }
  document.getElementById('provider-refresh').addEventListener('click', checkProvider);

  /* Atalhos declarativos: [data-goto] navega; [data-example] preenche uma descricao de exemplo. */
  document.addEventListener('click', (event) => {
    const go = event.target.closest('[data-goto]');
    if (go) { show(go.dataset.goto); return; }
    const focusBtn = event.target.closest('[data-focus]');
    if (focusBtn) {
      if (document.getElementById('t-studio').classList.contains('brief-off')) setBrief(true);
      document.getElementById(focusBtn.dataset.focus).focus();
      return;
    }
    const example = event.target.closest('[data-example]');
    if (example) {
      const field = document.getElementById(example.dataset.example);
      if (!field) return;
      field.value = example.dataset.text || '';
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.focus();
    }
  });

  /* Painel de geracao do Thumbnail Studio: recolhe para dar mais area ao canvas. */
  const briefToggle = document.getElementById('t-brieftoggle');
  const studioGrid = document.getElementById('t-studio');
  function setBrief(open) {
    studioGrid.classList.toggle('brief-off', !open);
    briefToggle.setAttribute('aria-pressed', String(open));
    store.set('brief', open ? 'open' : 'closed');
    window.dispatchEvent(new Event('resize')); // o canvas recalcula o ajuste de zoom
  }
  document.addEventListener('keydown', (event) => {
    // Em janelas medias o painel e uma gaveta: Esc fecha (nao interfere com modais).
    if (event.key !== 'Escape' || ui.openModals().length) return;
    if (window.matchMedia('(min-width: 1100px) and (max-width: 1279px)').matches && !studioGrid.classList.contains('brief-off')
        && !document.getElementById('view-thumbnail').hidden) setBrief(false);
  });
  briefToggle.addEventListener('click', () => setBrief(briefToggle.getAttribute('aria-pressed') !== 'true'));
  // Em janelas estreitas o painel e uma gaveta: comeca fechado ate o usuario pedir.
  const narrow = window.matchMedia('(min-width: 1100px) and (max-width: 1279px)').matches;
  if (store.get('brief') === 'closed' || (narrow && store.get('brief') !== 'open')) setBrief(false);

  /* Navegacao interna de Configuracoes. */
  const setNav = document.querySelectorAll('.set-nav button');
  for (const btn of setNav) {
    btn.addEventListener('click', () => {
      setNav.forEach((other) => other.classList.toggle('active', other === btn));
      document.getElementById(btn.dataset.target).scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  async function checkProvider() {
    const dot = document.getElementById('provider-dot');
    const name = document.getElementById('provider-name');
    const sub = document.getElementById('provider-sub');
    dot.className = 'dot wait';
    try {
      const data = await api.get('/api/provider');
      const ok = data.health && data.health.ok;
      dot.className = `dot ${ok ? 'ok' : 'err'}`;
      name.textContent = data.name === 'simulated' ? i18n.t('provider.simulated') : data.name;
      const state = ok ? (data.capabilities && data.capabilities.note ? data.capabilities.note : i18n.t('provider.available')) : i18n.t('provider.unavailable');
      sub.textContent = state;
      document.querySelector('.provider-card').title = i18n.t('provider.cardTitle', { name: name.textContent, state });
      Object.values(studios).forEach((studio) => studio.refreshProviderNote());
    } catch (err) {
      dot.className = 'dot err';
      name.textContent = i18n.t('provider.unavailableTitle');
      sub.textContent = '—';
      document.querySelector('.provider-card').title = i18n.t('provider.unavailableTitle');
    }
  }

  wirePrefs();
  i18n.onChange(() => checkProvider());
  studios.thumbnail.init();
  studios.image.init();
  window.studios = studios;
  window.app = { show, applyPrefs };
  const home = store.get('home') || 'last';
  show(home === 'last' ? (store.get('view') || 'image') : home, { focus: false });
  checkProvider();
  setInterval(checkProvider, 15000);
})();
