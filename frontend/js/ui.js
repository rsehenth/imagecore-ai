/* Utilitarios de UI: toast, modais (foco preso, Escape, clique fora), confirmacao e lightbox
   com navegacao entre imagens. */
(() => {
  let toastTimer = null;
  function showToast(text, kind) {
    const toast = document.getElementById('toast');
    toast.textContent = text;
    toast.className = `toast${kind ? ` ${kind}` : ''}`;
    toast.hidden = false;
    clearTimeout(toastTimer);
    // Erros e mensagens longas ficam mais tempo na tela.
    const ms = kind === 'error' ? 9000 : Math.min(9000, 3500 + text.length * 35);
    toastTimer = setTimeout(() => { toast.hidden = true; }, ms);
  }

  /* ------------------------------------------------------------ modais */
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const returnFocus = new WeakMap();
  const visible = (el) => el.offsetParent !== null || el === document.activeElement;

  function openModal(modal) {
    returnFocus.set(modal, document.activeElement);
    modal.classList.remove('hidden');
    const card = modal.querySelector('.modal-card') || modal;
    const target = card.querySelector('[data-autofocus]')
      || card.querySelector('.modal-body :is(input, select, textarea)')
      || card.querySelector('.btn.primary')
      || card.querySelector(FOCUSABLE);
    if (target) target.focus({ preventScroll: true });
  }
  function closeModal(modal) {
    if (modal.classList.contains('hidden')) return;
    modal.classList.add('hidden');
    modal.dispatchEvent(new Event('dismiss'));
    const back = returnFocus.get(modal);
    if (back && back.isConnected && typeof back.focus === 'function') back.focus({ preventScroll: true });
  }
  function openModals() { return [...document.querySelectorAll('.modal:not(.hidden)')]; }

  document.addEventListener('click', (event) => {
    const modal = event.target.closest('.modal');
    if (!modal) return;
    if (event.target === modal || event.target.closest('[data-close]')) closeModal(modal);
  });
  document.addEventListener('keydown', (event) => {
    const modals = openModals();
    if (!modals.length) return;
    const top = modals[modals.length - 1];
    if (event.key === 'Escape') { closeModal(top); return; }
    if (event.key !== 'Tab') return;
    // Foco preso dentro do dialogo aberto.
    const items = [...top.querySelectorAll(FOCUSABLE)].filter(visible);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    else if (!top.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
  });

  /* ------------------------------------------------------------ confirmacao */
  const confirmModal = document.getElementById('confirm-modal');
  const confirmMessage = document.getElementById('confirm-message');
  const confirmOk = document.getElementById('confirm-ok');
  let confirmResolve = null;
  confirmOk.addEventListener('click', () => { const resolve = confirmResolve; confirmResolve = null; closeModal(confirmModal); if (resolve) resolve(true); });
  confirmModal.addEventListener('dismiss', () => { if (confirmResolve) confirmResolve(false); confirmResolve = null; });
  function confirmDialog({ title, message, okLabel }) {
    document.getElementById('confirm-title').textContent = title !== undefined ? title : i18n.t('common.confirm');
    confirmMessage.textContent = message;
    confirmOk.textContent = okLabel !== undefined ? okLabel : i18n.t('common.confirm');
    confirmResolve = null;
    openModal(confirmModal);
    return new Promise((resolve) => { confirmResolve = resolve; });
  }

  /* ------------------------------------------------------------ lightbox (com navegacao opcional) */
  const lightbox = document.getElementById('lightbox');
  const lightboxImg = document.getElementById('lightbox-img');
  const lightboxMeta = document.getElementById('lightbox-meta');
  const lightboxTitle = document.getElementById('lightbox-title');
  const lightboxDownload = document.getElementById('lightbox-download');
  const lightboxPrev = document.getElementById('lightbox-prev');
  const lightboxNext = document.getElementById('lightbox-next');
  const lightboxCount = document.getElementById('lightbox-count');
  let lbSet = null; // { items: [{url,title,meta,filename}], index }

  function paintLightbox({ url, title, meta = '', filename = '' }) {
    lightboxImg.src = url;
    lightboxImg.alt = title;
    lightboxTitle.textContent = title !== undefined ? title : i18n.t('common.image');
    lightboxMeta.textContent = meta;
    lightboxDownload.href = url;
    lightboxDownload.setAttribute('download', filename || 'imagecore.png');
  }
  function stepLightbox(delta) {
    if (!lbSet) return;
    lbSet.index = (lbSet.index + delta + lbSet.items.length) % lbSet.items.length;
    paintLightbox(lbSet.items[lbSet.index]);
    lightboxCount.textContent = countText(lbSet.index + 1, lbSet.items.length);
  }
  const countText = (index, total) => i18n.t('common.of', { a: i18n.fmtNum(index), b: i18n.fmtNum(total) });
  /* openLightbox({url,title,meta,filename}) abre uma imagem; com `set` (lista) e `index`
     habilita as setas (botoes e teclado) para percorrer a biblioteca. */
  function openLightbox(opts) {
    const set = opts.set && opts.set.length > 1 ? opts.set : null;
    lbSet = set ? { items: set, index: Math.max(0, opts.index || 0) } : null;
    paintLightbox(set ? set[lbSet.index] : opts);
    lightboxPrev.hidden = lightboxNext.hidden = !set;
    lightboxCount.textContent = set ? countText(lbSet.index + 1, set.length) : '';
    openModal(lightbox);
  }
  lightboxPrev.addEventListener('click', () => stepLightbox(-1));
  lightboxNext.addEventListener('click', () => stepLightbox(1));
  document.addEventListener('keydown', (event) => {
    if (!lbSet || lightbox.classList.contains('hidden')) return;
    if (openModals().pop() !== lightbox) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); stepLightbox(-1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); stepLightbox(1); }
  });
  lightbox.addEventListener('dismiss', () => { lbSet = null; });

  /* ------------------------------------------------------------ formatadores */
  /* Estado de geracao traduzido por STATUS do backend (enum estavel). */
  const STATUS = {
    queued: () => i18n.t('status.queued'),
    running: () => i18n.t('status.running'),
    succeeded: () => i18n.t('status.succeeded'),
    failed: () => i18n.t('status.failed'),
    cancelled: () => i18n.t('status.cancelled'),
  };
  const statusLabel = (status) => (STATUS[status] ? STATUS[status]() : status || '');
  function statusClass(status) {
    if (status === 'succeeded') return 'ok';
    if (status === 'failed') return 'fail';
    if (status === 'running' || status === 'queued') return 'wait';
    return '';
  }
  function fmtDate(iso) {
    return i18n.fmtDate(iso);
  }
  function fmtBytes(n) {
    if (!n && n !== 0) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
  const icon = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  window.ui = { showToast, openModal, closeModal, openModals, confirmDialog, openLightbox, statusLabel, statusClass, fmtDate, fmtBytes, icon };
})();
